"""Hashimoto (1983) residue trajectory scoring without inferred inputs.

The source extract supplies one admitted row per measured oxide. Runtime
results are keyed by experiment id so one simulator trajectory can serve the
oxide records from the same physical run. Until the printed free-evaporation
area is available, the batch emits typed refusal records instead of inventing
an area or using the simulator's fallback geometry.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import yaml

from simulator.lab_schedule import _pressure_floor_notice_mbar
from simulator.yaml_cache import load_cached_safe_yaml


SOURCE_ID = "kems-015-hashimoto-1983"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRESET_PATH = ROOT / "data/literature/reproduction-inputs/hashimoto_1983.yaml"
DEFAULT_OBSERVATIONS_PATH = (
    ROOT / "data/literature/extracts-v2/kems-015-hashimoto-1983.yaml"
)
REQUIRED_STARTING_OXIDES = ("SiO2", "Al2O3", "FeO", "MgO", "CaO")
ADMITTED_RECORD_COUNT = 120
RECORD_UNITS = "wt%"


class Hashimoto1983Refusal(ValueError):
    """Named refusal for unresolved Hashimoto inputs or runtime output."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        self.detail = str(detail)
        super().__init__(f"{self.reason}: {self.detail}" if self.detail else self.reason)


@dataclass(frozen=True)
class HashimotoObservation:
    observation_id: str
    experiment_id: str
    source_id: str
    T_K: float
    time_min: float
    oxide: str
    published_wt_pct: float


@dataclass(frozen=True)
class HashimotoScoreRecord:
    observation_id: str
    source_id: str
    T_K: float
    time_min: float
    oxide: str
    published_wt_pct: float
    sim_wt_pct: float | None
    residual: float | None
    units: str
    notice_set: frozenset[str]
    refusal_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source_id": self.source_id,
            "T_K": self.T_K,
            "time_min": self.time_min,
            "oxide": self.oxide,
            "published_wt_pct": self.published_wt_pct,
            "sim_wt_pct": self.sim_wt_pct,
            "residual": self.residual,
            "units": self.units,
            "notice_set": sorted(self.notice_set),
            "refusal_reason": self.refusal_reason,
        }


def load_hashimoto_preset(
    path: str | Path = DEFAULT_PRESET_PATH,
) -> dict[str, Any]:
    """Load the source-backed preset, allowing its area to remain unresolved."""

    preset_path = Path(path)
    try:
        document = load_cached_safe_yaml(preset_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise Hashimoto1983Refusal("preset_unreadable", str(exc)) from exc
    if not isinstance(document, Mapping):
        raise Hashimoto1983Refusal("preset_root_not_mapping")
    if document.get("schema_version") != "hashimoto_1983_trajectory.v1":
        raise Hashimoto1983Refusal("unsupported_preset_schema")
    validate_starting_composition(document)
    return dict(document)


def validate_starting_composition(preset: Mapping[str, Any]) -> Mapping[str, float]:
    raw = preset.get("starting_composition_wt_pct")
    if not isinstance(raw, Mapping):
        raise Hashimoto1983Refusal("missing_starting_composition")
    composition: dict[str, float] = {}
    for oxide in REQUIRED_STARTING_OXIDES:
        if oxide not in raw:
            raise Hashimoto1983Refusal("missing_starting_oxide", oxide)
        composition[oxide] = _finite_number(raw[oxide], f"starting_composition.{oxide}")
    if any(value <= 0.0 for value in composition.values()):
        raise Hashimoto1983Refusal("invalid_starting_oxide_wt_pct")
    if not math.isclose(sum(composition.values()), 100.0, rel_tol=0.0, abs_tol=0.01):
        raise Hashimoto1983Refusal("starting_composition_not_100_wt_pct")
    return composition


def require_runtime_inputs(preset: Mapping[str, Any]) -> None:
    """Refuse if the source does not supply a positive molten exposed area."""

    validate_starting_composition(preset)
    geometry = preset.get("free_evaporation_geometry")
    area = geometry.get("exposed_melt_area_m2") if isinstance(geometry, Mapping) else None
    if area is None:
        pending = geometry.get("pending_observation_ids", ()) if isinstance(geometry, Mapping) else ()
        raise Hashimoto1983Refusal(
            "missing_exposed_melt_area",
            ", ".join(str(item) for item in pending),
        )
    if _finite_number(area, "free_evaporation_geometry.exposed_melt_area_m2") <= 0.0:
        raise Hashimoto1983Refusal("invalid_exposed_melt_area")
    vacuum = preset.get("vacuum")
    if not isinstance(vacuum, Mapping) or vacuum.get("units") != "Torr":
        raise Hashimoto1983Refusal("unsupported_runtime_pressure_unit")
    pressure_mbar = _finite_number(
        vacuum.get("runtime_pressure_mbar"),
        "vacuum.runtime_pressure_mbar",
    )
    floor_notice = _pressure_floor_notice_mbar(pressure_mbar)
    if floor_notice is not None:
        _, detail = floor_notice
        raise Hashimoto1983Refusal(
            "runtime_pressure_below_implemented_floor",
            detail,
        )


def load_admitted_observations(
    path: str | Path = DEFAULT_OBSERVATIONS_PATH,
) -> tuple[HashimotoObservation, ...]:
    """Read admitted residue rows only, ignoring superseded extract copies."""

    extract_path = Path(path)
    try:
        document = load_cached_safe_yaml(extract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise Hashimoto1983Refusal("observations_unreadable", str(exc)) from exc
    rows = document.get("observations") if isinstance(document, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise Hashimoto1983Refusal("observations_missing")

    observations: list[HashimotoObservation] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        quantity = _nested(row, "identity", "quantity")
        admission = row.get("admission")
        if (
            not isinstance(quantity, Mapping)
            or quantity.get("value") != "residue_component_composition"
            or not isinstance(admission, Mapping)
            or admission.get("status") != "admitted"
        ):
            continue

        observation_id = _required_text(row.get("observation_id"), "observation_id")
        subtype = _nested(row, "identity", "subtype")
        if not isinstance(subtype, Mapping) or subtype.get("value") != "oxide_wt_percent":
            raise Hashimoto1983Refusal("unsupported_residue_subtype", observation_id)
        locator = row.get("locator")
        if not isinstance(locator, Mapping) or str(locator.get("table")) != "3":
            raise Hashimoto1983Refusal("unexpected_residue_table", observation_id)
        source_id = _required_text(row.get("source_id"), "source_id")
        if source_id != SOURCE_ID:
            raise Hashimoto1983Refusal("unexpected_source_id", source_id)
        species = _nested(row, "identity", "species")
        if not isinstance(species, Mapping):
            raise Hashimoto1983Refusal("missing_observation_oxide", observation_id)
        oxide = _required_text(species.get("formula"), "identity.species.formula")
        if oxide not in REQUIRED_STARTING_OXIDES:
            raise Hashimoto1983Refusal("unsupported_residue_oxide", oxide)
        conditions = row.get("point_conditions")
        if not isinstance(conditions, Mapping):
            raise Hashimoto1983Refusal("missing_point_conditions", observation_id)
        T_K = _located_value(conditions.get("temperature_K"), "temperature_K")
        time_min = _located_value(conditions.get("time_min"), "time_min")
        if T_K <= 0.0 or time_min <= 0.0:
            raise Hashimoto1983Refusal("invalid_trajectory_coordinate", observation_id)
        value = row.get("value")
        if not isinstance(value, Mapping) or value.get("kind") != "point":
            raise Hashimoto1983Refusal("missing_published_residue_value", observation_id)
        published_wt_pct = _finite_number(value.get("point"), "value.point")
        if not 0.0 <= published_wt_pct <= 100.0:
            raise Hashimoto1983Refusal("invalid_published_residue_value", observation_id)
        observations.append(
            HashimotoObservation(
                observation_id=observation_id,
                experiment_id=_required_text(row.get("experiment_id"), "experiment_id"),
                source_id=source_id,
                T_K=T_K,
                time_min=time_min,
                oxide=oxide,
                published_wt_pct=published_wt_pct,
            )
        )
    _validate_admitted_cohort(observations)
    return tuple(observations)


def score_hashimoto_1983(
    *,
    preset: Mapping[str, Any] | None = None,
    observations: Sequence[HashimotoObservation] | None = None,
    runtime_outputs: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[HashimotoScoreRecord, ...]:
    """Emit a residual or typed refusal for every admitted Table 3 row.

    ``runtime_outputs`` maps each source experiment id to the process
    simulator's final oxide wt-percent map. It is explicit so absent output is
    refused rather than confused with a zero residual. A supplied map also
    permits synthetic-runtime contract tests while the source area is absent.
    """

    active_preset = preset if preset is not None else load_hashimoto_preset()
    active_observations = (
        observations if observations is not None else load_admitted_observations()
    )
    _validate_admitted_cohort(active_observations)
    validate_starting_composition(active_preset)
    input_refusal: Hashimoto1983Refusal | None = None
    if runtime_outputs is None:
        try:
            require_runtime_inputs(active_preset)
        except Hashimoto1983Refusal as exc:
            input_refusal = exc

    records: list[HashimotoScoreRecord] = []
    for observation in active_observations:
        refusal_reason: str | None = None
        simulated: float | None = None
        notices: set[str] = set()
        if input_refusal is not None:
            refusal_reason = input_refusal.reason
            notices.add(input_refusal.reason)
        elif runtime_outputs is None:
            refusal_reason = "missing_runtime_output"
            notices.add(refusal_reason)
        else:
            run_output = runtime_outputs.get(observation.experiment_id)
            if not isinstance(run_output, Mapping) or observation.oxide not in run_output:
                refusal_reason = "missing_runtime_output"
                notices.add(refusal_reason)
            else:
                try:
                    candidate = _finite_number(
                        run_output[observation.oxide],
                        f"runtime_outputs.{observation.experiment_id}.{observation.oxide}",
                    )
                    if not 0.0 <= candidate <= 100.0:
                        raise Hashimoto1983Refusal("invalid_runtime_output")
                    simulated = candidate
                except Hashimoto1983Refusal as exc:
                    refusal_reason = exc.reason
                    notices.add(exc.reason)
        residual = (
            None
            if simulated is None
            else simulated - observation.published_wt_pct
        )
        records.append(
            HashimotoScoreRecord(
                observation_id=observation.observation_id,
                source_id=observation.source_id,
                T_K=observation.T_K,
                time_min=observation.time_min,
                oxide=observation.oxide,
                published_wt_pct=observation.published_wt_pct,
                sim_wt_pct=simulated,
                residual=residual,
                units=RECORD_UNITS,
                notice_set=frozenset(notices),
                refusal_reason=refusal_reason,
            )
        )
    return tuple(records)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Path, default=DEFAULT_PRESET_PATH)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    records = score_hashimoto_1983(
        preset=load_hashimoto_preset(args.preset),
        observations=load_admitted_observations(args.observations),
    )
    payload = json.dumps([record.as_dict() for record in records], indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload, encoding="utf-8")
    return 0


def _located_value(raw: Any, field: str) -> float:
    if not isinstance(raw, Mapping):
        raise Hashimoto1983Refusal("missing_point_condition", field)
    state = raw.get("state")
    locator = raw.get("locator")
    if (
        not isinstance(state, Mapping)
        or state.get("tag") != "value"
        or not isinstance(locator, Mapping)
    ):
        raise Hashimoto1983Refusal("unlocated_point_condition", field)
    return _finite_number(state.get("value"), f"point_conditions.{field}")


def _validate_admitted_cohort(
    observations: Sequence[HashimotoObservation],
) -> None:
    if len(observations) != ADMITTED_RECORD_COUNT:
        raise Hashimoto1983Refusal(
            "admitted_record_count_mismatch",
            f"expected {ADMITTED_RECORD_COUNT}, got {len(observations)}",
        )
    observation_ids = [row.observation_id for row in observations]
    if len(set(observation_ids)) != len(observation_ids):
        raise Hashimoto1983Refusal("duplicate_admitted_observation")
    experiment_oxides = [(row.experiment_id, row.oxide) for row in observations]
    if len(set(experiment_oxides)) != len(experiment_oxides):
        raise Hashimoto1983Refusal("duplicate_experiment_oxide")


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Hashimoto1983Refusal("missing_required_text", field)
    return value.strip()


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise Hashimoto1983Refusal("invalid_numeric_input", field)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise Hashimoto1983Refusal("invalid_numeric_input", field) from exc
    if not math.isfinite(number):
        raise Hashimoto1983Refusal("invalid_numeric_input", field)
    return number


if __name__ == "__main__":
    raise SystemExit(main())
