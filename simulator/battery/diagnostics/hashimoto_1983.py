"""Hashimoto (1983) residue trajectory scoring with explicit runtime flags.

The source extract supplies one admitted row per measured oxide. Runtime
results are keyed by experiment id so one simulator trajectory can serve the
oxide records from the same physical run. An unprinted free-evaporation area
uses the runtime DP-007 crucible face and carries that run's typed flag into
each residual record.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any

import yaml

from simulator.battery.migrate import to_plain, torr_to_pa
from simulator.battery.enums import NoticeKind, Quantity
from simulator.battery.records import Notice, NoticePolicy
from simulator.battery.source_declarations import (
    HASHIMOTO_RESIDUE_PREDICTOR,
    declared_residue_predictor,
)
from simulator.lab_schedule import _pressure_domain_notice_mbar
from simulator.physical_constants import PA_PER_MBAR
from simulator.scalar_boundary import require_finite_float
from simulator.source_ids import HASHIMOTO_SOURCE_ID as SOURCE_ID
from simulator.yaml_cache import load_cached_safe_yaml


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRESET_PATH = ROOT / "data/literature/reproduction-inputs/hashimoto_1983.yaml"
DEFAULT_OBSERVATIONS_PATH = ROOT / f"data/literature/extracts-v2/{SOURCE_ID}.yaml"
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
    policy_notice: Notice | None = None
    policy_detail: str | None = None
    pressure_domain_notice: Mapping[str, Any] | None = None
    flagged_strata: frozenset[str] = frozenset()
    refusal_reason: str | None = None

    @property
    def clean_measured_headline_eligible(self) -> bool:
        return self.residual is not None and not self.flagged_strata

    def as_dict(self) -> dict[str, Any]:
        notice_details: list[dict[str, Any]] = []
        if self.policy_notice is not None:
            policy = self.policy_notice.policy
            notice_details.append(
                {
                    "kind": self.policy_notice.kind.value,
                    "reason": self.policy_notice.reason,
                    "detail": self.policy_detail or "",
                    "policy": None if policy is None else to_plain(policy),
                }
            )
        if self.pressure_domain_notice is not None:
            notice_details.append(dict(self.pressure_domain_notice))
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
            "notice_details": notice_details,
            "flagged_strata": sorted(self.flagged_strata),
            "clean_measured_headline_eligible": (
                self.clean_measured_headline_eligible
            ),
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
        field = f"starting_composition.{oxide}"
        try:
            composition[oxide] = require_finite_float(raw[oxide], field)
        except ValueError as exc:
            raise Hashimoto1983Refusal("invalid_numeric_input", field) from exc
    if any(value <= 0.0 for value in composition.values()):
        raise Hashimoto1983Refusal("invalid_starting_oxide_wt_pct")
    if not math.isclose(sum(composition.values()), 100.0, rel_tol=0.0, abs_tol=0.01):
        raise Hashimoto1983Refusal("starting_composition_not_100_wt_pct")
    return composition


def require_runtime_inputs(
    preset: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Validate source inputs and return any pressure-domain notice."""

    validate_starting_composition(preset)
    geometry = preset.get("free_evaporation_geometry")
    area = geometry.get("exposed_melt_area_m2") if isinstance(geometry, Mapping) else None
    if area is not None:
        area_field = "free_evaporation_geometry.exposed_melt_area_m2"
        try:
            area_value = require_finite_float(area, area_field)
        except ValueError as exc:
            raise Hashimoto1983Refusal("invalid_numeric_input", area_field) from exc
        if area_value <= 0.0:
            raise Hashimoto1983Refusal("invalid_exposed_melt_area")
    vacuum = preset.get("vacuum")
    if not isinstance(vacuum, Mapping) or vacuum.get("units") != "Torr":
        raise Hashimoto1983Refusal("unsupported_runtime_pressure_unit")
    pressure_field = "vacuum.value"
    try:
        pressure_torr = require_finite_float(vacuum.get("value"), pressure_field)
    except ValueError as exc:
        raise Hashimoto1983Refusal("invalid_numeric_input", pressure_field) from exc
    if pressure_torr <= 0.0:
        raise Hashimoto1983Refusal("invalid_runtime_pressure")
    pressure_mbar = float(
        torr_to_pa(pressure_torr) / Decimal(str(PA_PER_MBAR))
    )
    return _pressure_domain_notice_mbar(pressure_mbar)


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
        if declared_residue_predictor(source_id) != HASHIMOTO_RESIDUE_PREDICTOR:
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
        value_field = "value.point"
        try:
            published_wt_pct = require_finite_float(value.get("point"), value_field)
        except ValueError as exc:
            raise Hashimoto1983Refusal("invalid_numeric_input", value_field) from exc
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
    simulator's final oxide wt-percent map and its ``run_metadata``. The
    metadata carries runtime-applied defaults so predicted residuals stay out
    of the clean measured headline. Missing output is refused rather than
    confused with a zero residual.
    """

    active_preset = preset if preset is not None else load_hashimoto_preset()
    active_observations = (
        observations if observations is not None else load_admitted_observations()
    )
    _validate_admitted_cohort(active_observations)
    validate_starting_composition(active_preset)
    input_refusal: Hashimoto1983Refusal | None = None
    pressure_domain_notice: Mapping[str, Any] | None = None
    try:
        pressure_domain_notice = require_runtime_inputs(active_preset)
    except Hashimoto1983Refusal as exc:
        input_refusal = exc

    records: list[HashimotoScoreRecord] = []
    for observation in active_observations:
        refusal_reason: str | None = None
        simulated: float | None = None
        notices: set[str] = set()
        policy_notice: Notice | None = None
        policy_detail: str | None = None
        flagged_strata: frozenset[str] = frozenset()
        if pressure_domain_notice is not None:
            notices.add(str(pressure_domain_notice["reason"]))
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
                    area_notice = _dp007_area_notice(run_output)
                    area_is_missing = (
                        isinstance(
                            active_preset.get("free_evaporation_geometry"),
                            Mapping,
                        )
                        and active_preset["free_evaporation_geometry"].get(
                            "exposed_melt_area_m2"
                        )
                        is None
                    )
                    if area_is_missing and area_notice is None:
                        raise Hashimoto1983Refusal(
                            "runtime_area_default_notice_missing"
                        )
                    candidate_field = (
                        f"runtime_outputs.{observation.experiment_id}."
                        f"{observation.oxide}"
                    )
                    try:
                        candidate = require_finite_float(
                            run_output[observation.oxide], candidate_field
                        )
                    except ValueError as exc:
                        raise Hashimoto1983Refusal(
                            "invalid_numeric_input", candidate_field
                        ) from exc
                    if not 0.0 <= candidate <= 100.0:
                        raise Hashimoto1983Refusal("invalid_runtime_output")
                    simulated = candidate
                    if area_notice is not None:
                        policy_detail = area_notice["detail"]
                        policy_notice = Notice(
                            kind=NoticeKind.KNOB_DEFAULTED,
                            affected_quantities=(
                                Quantity.RESIDUE_COMPONENT_COMPOSITION,
                            ),
                            reason=area_notice["reason"],
                            origin=observation.observation_id,
                            policy=area_notice["policy"],
                        )
                        from simulator.battery.score import (
                            flagged_strata as classify_flagged_strata,
                        )

                        notices.add(policy_notice.kind.value)
                        flagged_strata = frozenset(
                            classify_flagged_strata((policy_notice,))
                        )
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
                policy_notice=policy_notice,
                policy_detail=policy_detail,
                pressure_domain_notice=pressure_domain_notice,
                flagged_strata=flagged_strata,
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
    value_field = f"point_conditions.{field}"
    try:
        return require_finite_float(state.get("value"), value_field)
    except ValueError as exc:
        raise Hashimoto1983Refusal("invalid_numeric_input", value_field) from exc


def _dp007_area_notice(
    run_output: Mapping[str, Any],
) -> dict[str, Any] | None:
    run_metadata = run_output.get("run_metadata")
    runtime_notices = (
        run_metadata.get("runtime_notices")
        if isinstance(run_metadata, Mapping)
        else None
    )
    if not isinstance(runtime_notices, Sequence) or isinstance(
        runtime_notices, (str, bytes)
    ):
        return None
    for raw_notice in runtime_notices:
        if not isinstance(raw_notice, Mapping):
            continue
        if raw_notice.get("kind") != NoticeKind.KNOB_DEFAULTED.value:
            continue
        reason = raw_notice.get("reason")
        policy = raw_notice.get("policy")
        if (
            reason != "exposed_melt_area_not_printed"
            or not isinstance(policy, Mapping)
            or set(policy) != {"id", "version", "status", "value", "units"}
            or policy.get("id") != "DP-007"
            or policy.get("version") != 1
            or policy.get("status") != "canonical"
            or policy.get("units") != "m²"
        ):
            raise Hashimoto1983Refusal("invalid_runtime_area_default_policy")
        policy_field = "DP-007.value"
        try:
            policy_value = require_finite_float(policy.get("value"), policy_field)
        except ValueError as exc:
            raise Hashimoto1983Refusal("invalid_numeric_input", policy_field) from exc
        if policy_value <= 0.0:
            raise Hashimoto1983Refusal("invalid_runtime_area_default_policy")
        try:
            typed_policy = NoticePolicy(
                id=str(policy["id"]),
                version=policy["version"],
                status=str(policy["status"]),
                value=str(policy["value"]),
                units=str(policy["units"]),
            )
        except (TypeError, ValueError) as exc:
            raise Hashimoto1983Refusal(
                "invalid_runtime_area_default_policy", str(exc)
            ) from exc
        return {
            "kind": str(raw_notice["kind"]),
            "reason": str(reason),
            "detail": str(raw_notice.get("detail") or ""),
            "policy": typed_policy,
        }
    return None


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


if __name__ == "__main__":
    raise SystemExit(main())
