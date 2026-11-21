#!/usr/bin/env python3
"""Run and compare the Robinot (2026) benchtop reproduction deck."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import patch

# Running this file directly sets sys.path[0] to scripts/benchtop.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.campaigns import CampaignManager
from simulator.diagnostic_helpers.reproduction_compare import (
    TimeSeriesComparisonRefusal,
    _interpolate_runtime_sample,
    compare_o2_release_time_series,
    compare_time_series,
    compare_values,
    content_digest,
)
from simulator.diagnostic_helpers.timeseries_concordance import _interpolate
from simulator.diagnostic_helpers.vacuum_pyrolysis import (
    evaluate_vacuum_pyrolysis_comparison,
    load_vacuum_pyrolysis_observations,
)
from simulator.physical_constants import PA_PER_BAR, PA_PER_MBAR
from simulator.process_timestep import PROCESS_HOUR_S
from simulator.runner import (
    FURNACE_POWER_DRIVER_KEY,
    PRESET_PROVENANCE_METADATA_KEY,
    PresetRunSpec,
    PyrolysisRun,
    _load_preset_run_spec,
    _load_yaml,
)
from simulator.run_executor import RunExecutor
from simulator.thermal_budget import (
    FACE_FULL_SIDE,
    GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH,
    GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW,
)


PRESET_PATH = ROOT / "data/presets/vacuum_pyrolysis/robinot_2026.yaml"
PAPER_METRIC_DEFINITION = (
    "Section 3.2 Eq. (4) integrates the oxygen-production rate over time to "
    "obtain released O2 mass; Eq. (5) divides that amount by sample mass for "
    "the mass yield. Section 4.1, physical page 7, reports 35 mg O2 and "
    "31 mg O2/kWh, and describes a punctual pyrometer peak near 1800 C."
)
_COMPARATOR_CASE_ID = "robinot_2026"
_SOURCE_ID = "kems-044-robinot-2026"
_ANALYZER_VISIBLE_SCOPE = "analyzer_visible_o2_in_argon_carrier"


class RobinotReproductionError(ValueError):
    """Typed refusal for missing or inconsistent reproduction inputs."""


def _preset_spec() -> PresetRunSpec:
    spec = _load_preset_run_spec(PRESET_PATH, "faithful")
    if not isinstance(spec, PresetRunSpec):
        raise RobinotReproductionError("Robinot preset did not load as a run spec")
    return spec


def _required_corpus_paths(corpus_root: Path) -> dict[str, Path]:
    trajectory = _preset_spec().document["trajectory_comparison"]
    pins = trajectory["source_pins"]
    return {
        str(pin): corpus_root / str(item["path"])
        for pin, item in pins.items()
    }


def _read_source_series(path: Path) -> dict[str, list[dict[str, float]]]:
    grouped: dict[str, list[dict[str, float]]] = defaultdict(list)
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                grouped[str(row["series"])].append(
                    {
                        "time_min": float(row["x"]),
                        "value": float(row["y"]),
                        "uncertainty": float(row["y_unc"]),
                    }
                )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RobinotReproductionError(
            f"cannot read digitization series from {path}: {exc}"
        ) from exc
    if not grouped:
        raise RobinotReproductionError(f"digitization CSV has no rows: {path}")
    return dict(grouped)


def _load_corpus(corpus_root: Path, spec: PresetRunSpec) -> dict[str, Any]:
    trajectory = spec.document["trajectory_comparison"]
    pins = trajectory["source_pins"]
    file_paths = {
        str(pin): corpus_root / str(item["path"])
        for pin, item in pins.items()
    }
    digests: dict[str, str] = {}
    series_by_pin: dict[str, dict[str, list[dict[str, float]]]] = {}
    for pin, path in file_paths.items():
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise RobinotReproductionError(
                f"required corpus pin {pin} is unavailable: {path}"
            ) from exc
        expected = str(pins[pin]["sha256"])
        if digest != expected:
            raise RobinotReproductionError(
                f"corpus pin mismatch for {pin}: expected {expected}, got {digest}"
            )
        digests[pin] = digest
        if pin.endswith("_csv"):
            series_by_pin[pin] = _read_source_series(path)

    power_observable = trajectory["recorded_input_traces"][0]
    power_pin = str(power_observable["source_pin"])
    power_series_name = str(power_observable["source_series"])
    power_rows = series_by_pin[power_pin].get(power_series_name)
    if not power_rows:
        raise RobinotReproductionError(
            f"power series {power_series_name!r} is absent from corpus pin {power_pin}"
        )
    power_trace = {
        "role": power_observable["role"],
        "units": power_observable["units"],
        "source_pin": power_pin,
        "source_series": power_series_name,
        "point_fields": ["time_min", "expected_value"],
        "points": [
            [row["time_min"], row["value"]]
            for row in power_rows
        ],
    }
    return {
        "digests": digests,
        "file_paths": file_paths,
        "series_by_pin": series_by_pin,
        "power_trace": power_trace,
    }


def _source_observation_points(
    observable: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, float]],
) -> list[dict[str, Any]]:
    fields = [str(field) for field in observable["point_fields"]]
    indexes = {field: fields.index(field) for field in fields}
    curve = tuple(
        (
            float(row["time_min"]) * float(PROCESS_HOUR_S) / 60.0,
            float(row["value"]),
        )
        for row in source_rows
    )
    if not curve:
        raise RobinotReproductionError(
            f"source series {observable.get('source_series')!r} is empty"
        )
    points: list[dict[str, Any]] = []
    for index, row in enumerate(observable["points"]):
        time_min = float(row[indexes["time_min"]])
        expected = _interpolate(
            curve,
            time_min * float(PROCESS_HOUR_S) / 60.0,
            field_name=str(observable["source_series"]),
        )
        points.append(
            {
                "time_min": time_min,
                "expected_value": expected,
                "expected_uncertainty": {
                    "kind": "absolute",
                    "value": float(row[indexes["combined_uncertainty"]]),
                },
                "source_locator": {
                    "figure": (
                        "4a" if observable["source_pin"] == "f4a_csv" else "4b"
                    ),
                    "source_pin": str(observable["source_pin"]),
                    "provenance_pin": str(observable["provenance_pin"]),
                    "provenance_section": str(observable["provenance_section"]),
                    "source_series": str(observable["source_series"]),
                    "grid_index": index,
                    **(
                        {"prose_anchor_check": dict(observable["prose_anchor_check"])}
                        if isinstance(observable.get("prose_anchor_check"), Mapping)
                        else {}
                    ),
                },
            }
        )
    return points


def _time_min(step_index: int, dt_s: float) -> float:
    return float(step_index) * float(dt_s) / (float(PROCESS_HOUR_S) / 60.0)


def _run_case(
    *,
    spec: PresetRunSpec,
    corpus: Mapping[str, Any],
    max_steps: int | None = None,
    face_set: str | None = None,
    solar_absorptance: float | None = None,
) -> dict[str, Any]:
    schedule = deepcopy(dict(spec.lab_schedule))
    geometry = deepcopy(dict(spec.lab_geometry))
    driver = schedule[FURNACE_POWER_DRIVER_KEY]
    driver["trace"] = deepcopy(corpus["power_trace"])
    if face_set is not None:
        driver["radiating_face_set"] = face_set
        geometry["sample"]["radiating_face_set"] = face_set
    if solar_absorptance is not None:
        driver["solar_absorptance"] = float(solar_absorptance)

    duration_steps = int(
        round(float(spec.hours) * float(PROCESS_HOUR_S) / float(spec.process_dt_s))
    )
    steps = duration_steps if max_steps is None else int(max_steps)
    if steps < 0 or steps > duration_steps:
        raise RobinotReproductionError(
            f"max_steps must be between 0 and {duration_steps}; got {steps}"
        )
    run = PyrolysisRun(
        feedstock_id=spec.feedstock_id,
        campaign="C0",
        hours=steps,
        mass_kg=spec.mass_kg,
        backend_name="alphamelts",
        setpoints_patch={"lab_geometry": geometry},
        lab_schedule=schedule,
        run_metadata_overrides={
            PRESET_PROVENANCE_METADATA_KEY: dict(spec.provenance),
            "started_at_utc": None,
        },
        process_dt_s=spec.process_dt_s,
    )

    power_records: list[dict[str, Any]] = []
    executions = []
    original_get_temp_target = CampaignManager.get_temp_target
    original_execute_session = RunExecutor.execute_session

    def capture_power_temperature(
        manager: CampaignManager,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = original_get_temp_target(manager, *args, **kwargs)
        record = manager.__dict__.get("_power_temperature_for_step")
        if isinstance(record, Mapping):
            power_records.append(deepcopy(dict(record)))
        return result

    def capture_execution(
        executor: RunExecutor,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        execution = original_execute_session(executor, *args, **kwargs)
        executions.append(execution)
        return execution

    with (
        patch.object(CampaignManager, "get_temp_target", capture_power_temperature),
        patch.object(RunExecutor, "execute_session", capture_execution),
    ):
        runtime_result = run.run()
    if not executions:
        raise RobinotReproductionError("runner returned without a RunExecution")
    execution = executions[-1]
    runtime_result.get("run_metadata", {}).pop("started_at_utc", None)

    per_step: list[dict[str, Any]] = []
    rate_samples = [{"time_min": 0.0, "o2_release_mol_min": 0.0}]
    cumulative_samples = [{"time_min": 0.0, "value": 0.0}]
    for index, snapshot in enumerate(execution.snapshots, start=1):
        time_min = _time_min(index, spec.process_dt_s)
        summary = (
            execution.per_hour[index - 1]
            if index - 1 < len(execution.per_hour)
            else {}
        )
        pressure_bar = summary.get("P_total_bar")
        pressure_mbar = (
            None
            if pressure_bar is None
            else float(pressure_bar) * PA_PER_BAR / PA_PER_MBAR
        )
        rate_samples.append(
            {
                "time_min": time_min,
                "o2_release_mol_min": (
                    float(snapshot.melt_offgas_O2_mol_hr)
                    / (float(PROCESS_HOUR_S) / 60.0)
                ),
            }
        )
        cumulative_samples.append(
            {
                "time_min": time_min,
                "value": float(snapshot.oxygen_produced_kg) * 1_000_000.0,
            }
        )
        per_step.append(
            {
                "t": time_min,
                "chamber_pressure_mbar": pressure_mbar,
                "melt_temperature_C": float(snapshot.temperature_C),
                "o2_release_mol_min": rate_samples[-1]["o2_release_mol_min"],
                "cumulative_o2_mg": cumulative_samples[-1]["value"],
            }
        )
    for record in power_records:
        record["time_min"] = (
            None
            if record.get("time_s") is None
            else float(record["time_s"]) / (float(PROCESS_HOUR_S) / 60.0)
        )
    return {
        "runtime": {
            "status": str(execution.status),
            "reason": str(execution.reason or ""),
            "error_message": str(execution.error_message or ""),
            "steps_completed": len(execution.snapshots),
            "steps_requested": steps,
            "process_dt_s": float(spec.process_dt_s),
            "duration_completed_min": _time_min(
                len(execution.snapshots), spec.process_dt_s
            ),
            "backend_status": str(execution.backend_status),
        },
        "result": runtime_result,
        "per_step": per_step,
        "rate_samples": rate_samples,
        "cumulative_samples": cumulative_samples,
        "power_temperature_records": power_records,
    }


def _base_record(record: Any, *, mode: str) -> dict[str, Any]:
    comparison = record.as_dict()
    coordinate = record.coordinate
    t: Any = coordinate.get("time_min")
    if t is None and "window_start_min" in coordinate:
        t = {
            "start_min": coordinate["window_start_min"],
            "end_min": coordinate["window_end_min"],
        }
    flags = [] if record.status == "match" else [record.status]
    if record.status == "out-of-domain":
        flags.append("runtime_time_coverage_missing")
    return {
        "t": t,
        "observable": record.observable_id,
        "published": record.expected_value,
        "simulated": record.actual_value,
        "residual": record.residual,
        "flags": flags,
        "mode": mode,
        "units": record.units,
        "status": record.status,
        "coordinate": dict(coordinate),
        "source_locator": comparison["source_locator"],
        "expected_uncertainty": comparison["expected_uncertainty"],
    }


def _pressure_at(
    time_min: float,
    pressure_samples: Sequence[Mapping[str, Any]],
) -> float | None:
    available = [
        (float(sample["time_min"]), float(sample["value_mbar"]))
        for sample in pressure_samples
        if sample.get("value_mbar") is not None
    ]
    if not available:
        return None
    times = [time for time, _ in available]
    try:
        return _interpolate_runtime_sample(time_min, available, times)
    except TimeSeriesComparisonRefusal:
        return None


def _compare_trajectory(
    *,
    spec: PresetRunSpec,
    corpus: Mapping[str, Any],
    run_data: Mapping[str, Any],
    mode: str,
) -> list[dict[str, Any]]:
    trajectory = spec.document["trajectory_comparison"]
    observations = {
        str(observable["observable_id"]): observable
        for observable in trajectory["observables"]
    }
    runtime_summary = run_data["runtime"]
    maximum_time = max(
        float(sample["time_min"]) for sample in run_data["cumulative_samples"]
    )
    recipe = {
        "preset": spec.document,
        "corpus_sha256": corpus["digests"],
        "mode": mode,
    }
    records: list[dict[str, Any]] = []
    for observable_id, semantics, units, runtime_key in (
        (
            "robinot_o2_ppm_time_series",
            "rate",
            "ppm",
            "rate_samples",
        ),
        (
            "robinot_cumulative_o2_mass_time_series",
            "cumulative",
            "mg",
            "cumulative_samples",
        ),
    ):
        observable = observations[observable_id]
        source_pin = str(observable["source_pin"])
        source_series = str(observable["source_series"])
        source_rows = corpus["series_by_pin"][source_pin][source_series]
        observation_points = _source_observation_points(observable, source_rows)
        supported = [
            point
            for point in observation_points
            if float(point["time_min"]) <= maximum_time
        ]
        runtime_samples = run_data[runtime_key]
        pressure_samples = [
            {
                "time_min": float(sample["t"]),
                "value_mbar": sample["chamber_pressure_mbar"],
            }
            for sample in run_data["per_step"]
        ]
        initial_pressure_points = spec.lab_schedule.get("chamber_pressure_mbar", ())
        if initial_pressure_points:
            pressure_samples.insert(
                0,
                {
                    "time_min": 0.0,
                    "value_mbar": float(initial_pressure_points[0]["value"]),
                },
            )
        locator = {
            "figure": "4a" if observable["source_pin"] == "f4a_csv" else "4b",
            "source_pin": observable["source_pin"],
            "provenance_pin": observable["provenance_pin"],
            "provenance_section": observable["provenance_section"],
            "source_series": observable["source_series"],
            **(
                {"prose_anchor_check": dict(observable["prose_anchor_check"])}
                if isinstance(observable.get("prose_anchor_check"), Mapping)
                else {}
            ),
        }
        if len(supported) >= 2:
            if semantics == "rate":
                comparison_records = compare_o2_release_time_series(
                    case_id=_COMPARATOR_CASE_ID,
                    source_id=_SOURCE_ID,
                    observable_id=observable_id,
                    observation_points=supported,
                    runtime_rate_samples=runtime_samples,
                    argon_flow_nl_min=float(
                        spec.lab_schedule["gas_boundary"]["imposed_flow"]["value"]
                    ),
                    evidence_scope=_ANALYZER_VISIBLE_SCOPE,
                    source_locator=locator,
                    recipe=recipe,
                    observation={"points": supported},
                    runtime={"samples": runtime_samples, "run": runtime_summary},
                    assumed_input=True,
                    out_of_domain=runtime_summary["status"] != "ok",
                )
            else:
                comparison_records = compare_time_series(
                    case_id=_COMPARATOR_CASE_ID,
                    source_id=_SOURCE_ID,
                    observable_id=observable_id,
                    observation_points=supported,
                    runtime_samples=runtime_samples,
                    series_semantics=semantics,
                    units=units,
                    evidence_scope="source_side_cumulative_o2_mass",
                    source_locator=locator,
                    recipe=recipe,
                    observation={"points": supported},
                    runtime={"samples": runtime_samples, "run": runtime_summary},
                    species="O2",
                    assumed_input=True,
                    out_of_domain=runtime_summary["status"] != "ok",
                )
            records.extend(_base_record(item, mode=mode) for item in comparison_records)
        for point in observation_points[len(supported):]:
            missing_record = compare_values(
                case_id=_COMPARATOR_CASE_ID,
                source_id=_SOURCE_ID,
                observable_id=observable_id,
                species="O2",
                coordinate={"time_min": float(point["time_min"])},
                expected_value=float(point["expected_value"]),
                expected_uncertainty=point["expected_uncertainty"],
                actual_value=None,
                units=units,
                evidence_scope=(
                    _ANALYZER_VISIBLE_SCOPE
                    if semantics == "rate"
                    else "source_side_cumulative_o2_mass"
                ),
                source_locator=point["source_locator"],
                recipe=recipe,
                observation={"point": point},
                runtime={"samples": runtime_samples, "run": runtime_summary},
                assumed_input=True,
                out_of_domain=True,
            )
            record = _base_record(missing_record, mode=mode)
            record["chamber_pressure_mbar"] = _pressure_at(
                float(point["time_min"]), pressure_samples
            )
            records.append(record)
        for record in records:
            if (
                record["mode"] == mode
                and record["observable"] in {observable_id, f"{observable_id}:integral"}
                and isinstance(record["t"], (int, float))
            ):
                record["chamber_pressure_mbar"] = _pressure_at(
                    float(record["t"]), pressure_samples
                )
    return records


def _pyrometer_record(
    run_data: Mapping[str, Any],
    *,
    published_temperature_C: float,
    mode: str,
) -> dict[str, Any]:
    predictions = [
        record
        for record in run_data["power_temperature_records"]
        if record.get("temperature_C") is not None
    ]
    if not predictions:
        return {
            "t": None,
            "observable": "robinot_pyrometer_peak_temperature_C",
            "published": published_temperature_C,
            "simulated": None,
            "residual": None,
            "flags": ["power_temperature_prediction_unavailable"],
            "mode": mode,
            "units": "degC",
        }
    peak = max(predictions, key=lambda item: float(item["temperature_C"]))
    simulated = float(peak["temperature_C"])
    return {
        "t": None,
        "prediction_time_min": peak.get("time_min"),
        "observable": "robinot_pyrometer_peak_temperature_C",
        "published": published_temperature_C,
        "simulated": simulated,
        "residual": simulated - published_temperature_C,
        "flags": [
            "observed_peak_time_not_reported",
            "prediction_not_scored_as_observed_temperature",
        ],
        "mode": mode,
        "units": "degC",
    }


def _temperature_records(
    run_data: Mapping[str, Any],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    snapshots_by_time = {
        float(step["t"]): step["melt_temperature_C"]
        for step in run_data["per_step"]
    }
    power_records_by_time: dict[float, Mapping[str, Any]] = {}
    for record in run_data["power_temperature_records"]:
        if record.get("temperature_C") is not None:
            # Endpoint checks can request the same target again; the first
            # value is the one applied by the process step.
            power_records_by_time.setdefault(float(record["time_min"]), record)
    result = []
    for time_min, power_record in sorted(power_records_by_time.items()):
        temperature = power_record["temperature_C"]
        flags = ["prediction_not_observation"]
        if power_record.get("exceeds_furnace_ceiling"):
            flags.append("exceeds_furnace_ceiling")
        if power_record.get("exceeds_furnace_max"):
            flags.append("exceeds_furnace_max")
        result.append(
            {
                "t": time_min,
                "observable": "predicted_melt_temperature_C",
                "published": None,
                "simulated": float(temperature),
                "residual": None,
                "flags": flags,
                "mode": mode,
                "units": "degC",
                "snapshot_temperature_C": snapshots_by_time.get(time_min),
                "face_set": power_record.get("face_set"),
            }
        )
    return result


def _mass_integral_record(
    spec: PresetRunSpec,
    run_data: Mapping[str, Any],
) -> dict[str, Any]:
    sidecar_path = ROOT / str(
        spec.document["comparison_contract"]["observation_sidecar_path"]
    )
    observations = load_vacuum_pyrolysis_observations(sidecar_path)
    comparison = evaluate_vacuum_pyrolysis_comparison(
        spec.document,
        observations,
        run_data["result"],
        feedstocks=_load_yaml(ROOT / "data/feedstocks.yaml"),
    )
    match = next(
        (
            record
            for record in comparison.records
            if record.observable_id == "robinot_o2_final_mass_kg"
        ),
        None,
    )
    if match is None:
        raise RobinotReproductionError(
            "the existing vacuum-pyrolysis comparator emitted no 35 mg O2 record"
        )
    record = _base_record(match, mode="imposed")
    record.update(
        {
            "t": None,
            "units": "kg",
            "reported_value_mg": (
                None
                if match.expected_value is None
                else float(match.expected_value) * 1_000_000.0
            ),
            "simulated_value_mg": (
                None
                if match.actual_value is None
                else float(match.actual_value) * 1_000_000.0
            ),
            "paper_definition": PAPER_METRIC_DEFINITION,
        }
    )
    return record


def _residual_stats(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[float]] = defaultdict(list)
    missing: dict[str, int] = defaultdict(int)
    for record in records:
        observable = str(record["observable"])
        residual = record.get("residual")
        if residual is None:
            missing[observable] += 1
        else:
            groups[observable].append(float(residual))
    stats: dict[str, Any] = {}
    for observable in sorted(set(groups) | set(missing)):
        values = groups[observable]
        stats[observable] = {
            "n": len(values),
            "n_missing": missing[observable],
            "mean": statistics.fmean(values) if values else None,
            "mae": statistics.fmean(abs(value) for value in values) if values else None,
            "rmse": (
                math.sqrt(statistics.fmean(value * value for value in values))
                if values
                else None
            ),
            "max_abs": max((abs(value) for value in values), default=None),
        }
    return stats


def _collect_notices_and_flags(value: Any, path: str = "run") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"notices", "flags"} and isinstance(item, (list, tuple)):
                for index, notice in enumerate(item):
                    found.append(
                        {
                            "path": f"{path}.{key}[{index}]",
                            "kind": key[:-1] if key.endswith("s") else key,
                            "value": notice,
                        }
                    )
            elif key in {"notices", "flags"} and isinstance(item, Mapping):
                for name, flag in item.items():
                    found.append(
                        {
                            "path": f"{path}.{key}.{name}",
                            "kind": "flag" if key == "flags" else "notice",
                            "value": flag,
                        }
                    )
            found.extend(_collect_notices_and_flags(item, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_collect_notices_and_flags(item, f"{path}[{index}]"))
    return found


def _run_notices_and_flags(
    name: str,
    run_data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    found = _collect_notices_and_flags(run_data["result"], name)
    for index, record in enumerate(run_data["power_temperature_records"]):
        time_min = record.get("time_min")
        for notice_index, notice in enumerate(record.get("notices") or ()):
            found.append(
                {
                    "path": f"{name}.power_temperature[{index}].notices[{notice_index}]",
                    "kind": "notice",
                    "time_min": time_min,
                    "value": notice,
                }
            )
        for flag in ("exceeds_furnace_ceiling", "exceeds_furnace_max"):
            if flag in record:
                found.append(
                    {
                        "path": f"{name}.power_temperature[{index}].{flag}",
                        "kind": "flag",
                        "time_min": time_min,
                        "value": bool(record[flag]),
                    }
                )
    return found


def _sensitivity_metrics(run_data: Mapping[str, Any]) -> dict[str, Any]:
    temperatures = [
        float(record["temperature_C"])
        for record in run_data["power_temperature_records"]
        if record.get("temperature_C") is not None
    ]
    final_mass = (
        run_data["per_step"][-1]["cumulative_o2_mg"]
        if run_data["per_step"]
        else None
    )
    return {
        "status": run_data["runtime"]["status"],
        "steps_completed": run_data["runtime"]["steps_completed"],
        "peak_predicted_temperature_C": max(temperatures, default=None),
        "last_cumulative_o2_mg": final_mass,
    }


def _spread(cases: Mapping[str, Mapping[str, Any]], key: str) -> dict[str, Any]:
    values = {
        name: float(metrics[key])
        for name, metrics in cases.items()
        if metrics.get(key) is not None
    }
    if not values:
        return {"min": None, "max": None, "spread": None, "by_case": {}}
    low = min(values.values())
    high = max(values.values())
    return {"min": low, "max": high, "spread": high - low, "by_case": values}


def _markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Robinot 2026 benchtop reproduction",
        "",
        f"- Runtime status: `{summary['runs']['imposed']['runtime']['status']}`",
        f"- Corpus input: `{summary['corpus']['root']}`",
        f"- Face set: `{summary['deck']['radiating_face_set']}`",
        f"- Process timestep: `{summary['deck']['process_dt_s']} s`",
        "- O2 metric: " + PAPER_METRIC_DEFINITION,
        "",
        "## Residual statistics",
        "",
        "| Observable | n | Missing | Mean | MAE | RMSE | Max abs |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for observable, stats in summary["residual_statistics"].items():
        values = [
            "-" if stats[key] is None else f"{stats[key]:.6g}"
            for key in ("mean", "mae", "rmse", "max_abs")
        ]
        lines.append(
            f"| {observable} | {stats['n']} | {stats['n_missing']} | "
            + " | ".join(values)
            + " |"
        )
    lines.extend(
        [
            "",
            "## Sensitivity",
            "",
            "| Quantity | Minimum | Maximum | Spread |",
            "|---|---:|---:|---:|",
        ]
    )
    for key, spread in summary["sensitivity"]["spread"].items():
        cells = [
            "-" if spread[name] is None else f"{spread[name]:.6g}"
            for name in ("min", "max", "spread")
        ]
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    lines.extend(["", "## Per-step pressure and trajectory records", ""])
    lines.extend(
        [
            "| t (min) | Chamber pressure (mbar) | O2 rate (mol/min) | "
            "Cumulative O2 (mg) | T prediction (C) |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for point in summary["runs"]["imposed"]["per_step"]:
        lines.append(
            "| {t:.6g} | {p} | {r:.6g} | {m:.6g} | {temp:.6g} |".format(
                t=point["t"],
                p=(
                    "-"
                    if point["chamber_pressure_mbar"] is None
                    else f"{point['chamber_pressure_mbar']:.6g}"
                ),
                r=point["o2_release_mol_min"],
                m=point["cumulative_o2_mg"],
                temp=point["melt_temperature_C"],
            )
        )
    lines.extend(["", "## Notices and flags", ""])
    notices = summary["notices_and_flags"]
    if not notices:
        lines.append("None emitted.")
    else:
        for item in notices:
            lines.append(
                f"- `{item['path']}` ({item['kind']}): "
                f"`{json.dumps(item['value'], sort_keys=True)}`"
            )
    lines.extend(
        [
            "",
            "All point-level residual records, full notices, source pins, and "
            "sensitivity details are in the JSON artifact.",
            "",
        ]
    )
    return "\n".join(lines)


def run_reproduction(
    *,
    corpus_root: Path,
    max_steps: int | None = None,
    include_sensitivity: bool = True,
) -> dict[str, Any]:
    corpus_root = Path(corpus_root).expanduser().resolve()
    spec = _preset_spec()
    corpus = _load_corpus(corpus_root, spec)
    imposed_run = _run_case(
        spec=spec,
        corpus=corpus,
        max_steps=max_steps,
    )
    records = _compare_trajectory(
        spec=spec,
        corpus=corpus,
        run_data=imposed_run,
        mode="imposed",
    )
    pyrometer = float(spec.lab_schedule["furnace_ceiling_C"])
    records.extend(_temperature_records(imposed_run, mode="imposed"))
    records.append(
        _pyrometer_record(
            imposed_run,
            published_temperature_C=pyrometer,
            mode="imposed",
        )
    )
    records.append(_mass_integral_record(spec, imposed_run))

    sensitivity_runs: dict[str, dict[str, Any]] = {"imposed_hot_upper_default": imposed_run}
    if include_sensitivity:
        sensitivity_runs["imposed_full_side_default"] = _run_case(
            spec=spec,
            corpus=corpus,
            max_steps=max_steps,
            face_set=FACE_FULL_SIDE,
        )
        sensitivity_runs["imposed_hot_upper_alpha_0.669"] = _run_case(
            spec=spec,
            corpus=corpus,
            max_steps=max_steps,
            solar_absorptance=GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW,
        )
        sensitivity_runs["imposed_hot_upper_alpha_0.906"] = _run_case(
            spec=spec,
            corpus=corpus,
            max_steps=max_steps,
            solar_absorptance=GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH,
        )
    sensitivity_cases = {
        name: _sensitivity_metrics(run_data)
        for name, run_data in sensitivity_runs.items()
    }
    notices_and_flags = [
        notice
        for name, run_data in sensitivity_runs.items()
        for notice in _run_notices_and_flags(name, run_data)
    ]
    summary = {
        "schema_version": "robinot_2026_benchtop_reproduction.v1",
        "paper": {
            "paper_id": spec.document["paper_id"],
            "citation_id": spec.document["paper_citation_id"],
            "metric_definition": PAPER_METRIC_DEFINITION,
            "reported_specific_energy_yield_mg_per_kWh": 31.0,
        },
        "corpus": {
            "root": str(corpus_root),
            "branch": spec.document["trajectory_comparison"]["corpus_branch"],
            "commit": spec.document["trajectory_comparison"]["corpus_commit"],
            "pins": corpus["digests"],
        },
        "deck": {
            "preset": str(PRESET_PATH.relative_to(ROOT)),
            "feedstock_id": spec.feedstock_id,
            "sample_mass_g": float(spec.provenance["sample_mass_g"]),
            "argon_flow_nl_min": float(
                spec.lab_schedule["gas_boundary"]["imposed_flow"]["value"]
            ),
            "pressure_driver": "imposed",
            "chamber_pressure_mbar": [
                float(point["value"])
                for point in spec.lab_schedule["chamber_pressure_mbar"]
            ],
            "radiating_face_set": spec.lab_geometry["sample"]["radiating_face_set"],
            "process_dt_s": float(spec.process_dt_s),
            "unprinted_thermal_knobs": "left unset for owner defaults and notices",
        },
        "runs": {"imposed": imposed_run},
        "records": records,
        "residual_statistics": _residual_stats(records),
        "sensitivity": {
            "cases": sensitivity_cases,
            "spread": {
                "peak_predicted_temperature_C": _spread(
                    sensitivity_cases, "peak_predicted_temperature_C"
                ),
                "last_cumulative_o2_mg": _spread(
                    sensitivity_cases, "last_cumulative_o2_mg"
                ),
            },
        },
        "notices_and_flags": notices_and_flags,
        "digests": {
            "recipe_sha256": content_digest(spec.document),
            "corpus_sha256": content_digest(corpus["digests"]),
            "runtime_sha256": content_digest(
                {
                    "imposed": imposed_run["result"],
                    "power_temperature_records": imposed_run[
                        "power_temperature_records"
                    ],
                }
            ),
        },
    }
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_corpus = os.environ.get("REGOLITH_CORPUS")
    if not default_corpus:
        default_corpus = str(Path.home() / "Repos/regolith-corpus")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path(default_corpus),
        help="corpus repository root (or set REGOLITH_CORPUS)",
    )
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = run_reproduction(corpus_root=args.corpus)
    except (RobinotReproductionError, ValueError, OSError) as exc:
        print(f"Robinot reproduction refused: {exc}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / "robinot_2026_reproduction.json"
    markdown_path = args.out / "robinot_2026_reproduction.md"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown_report(summary), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
