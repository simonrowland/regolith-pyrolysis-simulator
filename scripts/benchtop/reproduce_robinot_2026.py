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
from simulator.core import PyrolysisSimulator
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
from simulator.geometry import (
    _cylinder_volume_m3,
    _sphere_surface_area_m2_from_volume,
)
from simulator.runner import (
    FURNACE_POWER_DRIVER_KEY,
    PRESET_PROVENANCE_METADATA_KEY,
    PresetRunSpec,
    PyrolysisRun,
    _load_preset_run_spec,
    _load_yaml,
)
from simulator.run_executor import RunExecutor
from simulator.teststand import (
    DRIVER_APPARATUS,
    DRIVER_IMPOSED,
    NORMAL_LITRE_TEMPERATURE_K,
    _line_conductance_for_target_pressure_m3_s,
)
from simulator.thermal_budget import (
    FACE_FULL_SIDE,
    GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH,
    GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW,
    declared_benchtop_lengths_m,
    reported_temperature_observations,
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
# This controller-steered speed belongs to the limiting-case comparison; the
# printed NDX15 preset and imposed-pressure baseline remain unchanged.
_ROBINOT_LIMITING_PUMP_SPEED_M3_H = 15.1


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

    return {
        "digests": digests,
        "file_paths": file_paths,
        "series_by_pin": series_by_pin,
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


def _reproduction_step_budget(spec: PresetRunSpec) -> int:
    """Executor step count for one preset.

    ``PresetRunSpec.hours`` is already that count: ``duration_step_count``
    when the timestep is shorter than one hour, and ``int(duration_h)`` on
    the one-hour path. Multiplying by ``PROCESS_HOUR_S / process_dt_s``
    again asks for an hour of steps per process step.
    """

    return int(spec.hours)


def _run_case(
    *,
    spec: PresetRunSpec,
    max_steps: int | None = None,
    face_set: str | None = None,
    solar_absorptance: float | None = None,
    exposed_melt_area_m2: float | None = None,
    pressure_driver: str = DRIVER_IMPOSED,
    variant: str,
) -> dict[str, Any]:
    print(f"Robinot reproduction: starting {variant}", flush=True)
    schedule = deepcopy(dict(spec.lab_schedule))
    geometry = deepcopy(dict(spec.lab_geometry))
    driver = schedule[FURNACE_POWER_DRIVER_KEY]
    if face_set is not None:
        driver["radiating_face_set"] = face_set
        geometry["sample"]["radiating_face_set"] = face_set
    if solar_absorptance is not None:
        driver["solar_absorptance"] = float(solar_absorptance)
    case_flags: list[str] = []
    if exposed_melt_area_m2 is not None:
        geometry["sample"]["exposed_melt_area_m2"] = float(
            exposed_melt_area_m2
        )
        geometry["sample"]["exposed_melt_area_basis"] = (
            "assumed_equal_volume_molten_bead_sphere"
        )
        case_flags.append("exposed_melt_area_equal_volume_sphere_sensitivity")
    if pressure_driver == DRIVER_APPARATUS:
        schedule["gas_boundary"]["pressure_control"]["pressure_driver"] = (
            DRIVER_APPARATUS
        )
        pump = geometry["pump"]
        pump["pumping_speed_m3_s"] = (
            _ROBINOT_LIMITING_PUMP_SPEED_M3_H / float(PROCESS_HOUR_S)
        )
        pump["ultimate_pressure_pa"] = 0.0
        pump["balance_temperature_K"] = NORMAL_LITRE_TEMPERATURE_K
        pump["conductance_path"] = []
        case_flags.extend(
            [
                "limiting_case_unrestricted_conductance",
                "idealized_zero_ultimate_pressure",
                "normal_state_balance_temperature_assumption",
                "chamber_volume_not_required_for_steady_state",
                "reconciliation_conductance_diagnostic_only",
            ]
        )
    elif pressure_driver != DRIVER_IMPOSED:
        raise RobinotReproductionError(
            f"unsupported pressure driver for reproduction: {pressure_driver!r}"
        )

    duration_steps = _reproduction_step_budget(spec)
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
    teststand_pressure_records: list[dict[str, Any]] = []
    executions = []
    original_get_temp_target = CampaignManager.get_temp_target
    original_execute_session = RunExecutor.execute_session
    original_step_one_hour = PyrolysisSimulator._step_one_hour

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

    def capture_teststand_pressure(
        simulator: PyrolysisSimulator,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        snapshot = original_step_one_hour(simulator, *args, **kwargs)
        pressure_record = simulator.__dict__.get("last_teststand_pressure")
        if isinstance(pressure_record, Mapping):
            item = deepcopy(dict(pressure_record))
            item["time_min"] = _time_min(
                len(teststand_pressure_records) + 1,
                spec.process_dt_s,
            )
            teststand_pressure_records.append(item)
        return snapshot

    with (
        patch.object(CampaignManager, "get_temp_target", capture_power_temperature),
        patch.object(RunExecutor, "execute_session", capture_execution),
        patch.object(PyrolysisSimulator, "_step_one_hour", capture_teststand_pressure),
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
        "teststand_pressure_records": teststand_pressure_records,
        "geometry_assumptions": {
            "exposed_melt_area_m2": float(
                geometry["sample"]["exposed_melt_area_m2"]
            ),
            "exposed_melt_area_basis": str(
                geometry["sample"].get("exposed_melt_area_basis") or ""
            ),
        },
        "flags": case_flags,
        "pressure_case": {
            "driver": pressure_driver,
            "pump_speed_m3_h": (
                _ROBINOT_LIMITING_PUMP_SPEED_M3_H
                if pressure_driver == DRIVER_APPARATUS
                else None
            ),
            "pump_speed_m3_s": (
                _ROBINOT_LIMITING_PUMP_SPEED_M3_H / float(PROCESS_HOUR_S)
                if pressure_driver == DRIVER_APPARATUS
                else None
            ),
            "ultimate_pressure_pa": 0.0 if pressure_driver == DRIVER_APPARATUS else None,
            "balance_temperature_K": (
                NORMAL_LITRE_TEMPERATURE_K
                if pressure_driver == DRIVER_APPARATUS
                else None
            ),
            "conductance_path": [] if pressure_driver == DRIVER_APPARATUS else None,
            "chamber_volume_m3": geometry["chamber"].get("volume_m3"),
        },
    }


def _base_record(
    record: Any,
    *,
    mode: str,
    additional_flags: Sequence[str] = (),
) -> dict[str, Any]:
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
    flags.extend(additional_flags)
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
        unavailable_points = (
            observation_points[len(supported):]
            if len(supported) >= 2
            else observation_points
        )
        runtime_samples = run_data[runtime_key]
        pressure_samples = [
            {
                "time_min": float(sample["t"]),
                "value_mbar": sample["chamber_pressure_mbar"],
            }
            for sample in run_data["per_step"]
        ]
        initial_pressure_points = spec.lab_schedule.get("chamber_pressure_mbar", ())
        if mode == DRIVER_IMPOSED and initial_pressure_points:
            pressure_samples.insert(
                0,
                {
                    "time_min": 0.0,
                    "value_mbar": float(initial_pressure_points[0]["value"]),
                },
            )
        elif pressure_samples:
            first_pressure = next(
                (
                    sample["value_mbar"]
                    for sample in pressure_samples
                    if sample.get("value_mbar") is not None
                ),
                None,
            )
            if first_pressure is not None:
                pressure_samples.insert(
                    0,
                    {"time_min": 0.0, "value_mbar": first_pressure},
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
            records.extend(
                _base_record(
                    item,
                    mode=mode,
                    additional_flags=("assumed_input",)
                    if semantics == "rate"
                    else (),
                )
                for item in comparison_records
            )
        for point in unavailable_points:
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
            record = _base_record(
                missing_record,
                mode=mode,
                additional_flags=("assumed_input",)
                if semantics == "rate"
                else (),
            )
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


def _reported_pyrometer_observation(
    spec: PresetRunSpec,
) -> tuple[Mapping[str, Any] | None, str | None]:
    points = reported_temperature_observations(
        spec.lab_schedule.get("melt_temperature_C")
    )
    peak_points = [
        point
        for point in points
        if str(point.get("qualifier") or "") == "approximate_peak"
    ]
    if not peak_points:
        return None, "missing_reported_pyrometer_observation"
    if len(peak_points) != 1:
        return None, "ambiguous_reported_pyrometer_observation"
    point = peak_points[0]
    if point.get("value") is None:
        return None, "incomplete_reported_pyrometer_observation"
    return point, None


def _pyrometer_record(
    run_data: Mapping[str, Any],
    *,
    observation: Mapping[str, Any] | None,
    unavailable_reason: str | None,
    mode: str,
) -> dict[str, Any]:
    predictions = [
        record
        for record in run_data["power_temperature_records"]
        if record.get("temperature_C") is not None
    ]
    source_locator = (
        None
        if observation is None
        else {
            "preset_field": "lab_schedule.melt_temperature_C",
            "source_ref": observation.get("source_ref"),
            "citation_id": observation.get("citation_id"),
            "source_class": observation.get("source_class"),
            "t_h": observation.get("t_h"),
        }
    )
    if not predictions:
        return {
            "t": None,
            "observable": "robinot_pyrometer_peak_temperature_C",
            "published": (
                None if observation is None else float(observation["value"])
            ),
            "simulated": None,
            "residual": None,
            "flags": [
                "power_temperature_prediction_unavailable",
                *(
                    []
                    if observation is not None
                    else ["comparison_observation_unavailable", unavailable_reason]
                ),
            ],
            "comparison_status": "typed_unavailable",
            "unavailable_reason": "power_temperature_prediction_unavailable",
            "published_qualifier": (
                None if observation is None else observation.get("qualifier")
            ),
            "source_locator": source_locator,
            "mode": mode,
            "units": "degC",
        }
    peak = max(predictions, key=lambda item: float(item["temperature_C"]))
    simulated = float(peak["temperature_C"])
    if observation is None:
        return {
            "t": None,
            "prediction_time_min": peak.get("time_min"),
            "observable": "robinot_pyrometer_peak_temperature_C",
            "published": None,
            "simulated": simulated,
            "residual": None,
            "flags": ["comparison_observation_unavailable", unavailable_reason],
            "comparison_status": "typed_unavailable",
            "unavailable_reason": unavailable_reason,
            "mode": mode,
            "units": "degC",
            "source_locator": None,
        }
    published_temperature_C = float(observation["value"])
    return {
        "t": None,
        "prediction_time_min": peak.get("time_min"),
        "observation_time_h": observation.get("t_h"),
        "observable": "robinot_pyrometer_peak_temperature_C",
        "published": published_temperature_C,
        "simulated": simulated,
        "residual": simulated - published_temperature_C,
        "comparison_status": "available",
        "published_qualifier": observation.get("qualifier"),
        "source_locator": source_locator,
        "flags": [
            "approximate_peak_observation",
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
    observations: Mapping[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
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
    record = _base_record(match, mode=mode)
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
        observable = f"{record['mode']}:{record['observable']}"
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
    found.extend(
        _collect_notices_and_flags(
            run_data.get("teststand_pressure_records") or (),
            f"{name}.teststand_pressure",
        )
    )
    for index, flag in enumerate(run_data.get("flags") or ()):
        found.append(
            {
                "path": f"{name}.flags[{index}]",
                "kind": "flag",
                "value": flag,
            }
        )
    for index, record in enumerate(run_data["power_temperature_records"]):
        time_min = record.get("time_min")
        power_diagnostics = _collect_notices_and_flags(
            record,
            f"{name}.power_temperature[{index}]",
        )
        for diagnostic in power_diagnostics:
            diagnostic["time_min"] = time_min
            found.append(diagnostic)
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
        "exposed_melt_area_m2": run_data["geometry_assumptions"][
            "exposed_melt_area_m2"
        ],
        "exposed_melt_area_basis": run_data["geometry_assumptions"][
            "exposed_melt_area_basis"
        ],
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
    def format_value(value: Any) -> str:
        return "-" if value is None else f"{float(value):.6g}"

    lines = [
        "# Robinot 2026 benchtop reproduction",
        "",
        f"- Runtime status: `{summary['runs'][DRIVER_IMPOSED]['runtime']['status']}`",
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
            "Equal-volume molten-bead area uses the printed cylinder volume: "
            "`V = π(D/2)^2 H`; `A_bead = 4π(3V/(4π))^(2/3)`. This is an "
            "assumption sensitivity; the deck keeps the top-face area for its "
            "baseline melt-area input.",
            "",
            "| Variant | Melt area (m²) | Area basis | Peak T (°C) | Final O₂ (mg) |",
            "|---|---:|---|---:|---:|",
        ]
    )
    for name, case in summary["sensitivity"]["cases"].items():
        lines.append(
            f"| {name} | {format_value(case['exposed_melt_area_m2'])} | "
            f"{case['exposed_melt_area_basis']} | "
            f"{format_value(case['peak_predicted_temperature_C'])} | "
            f"{format_value(case['last_cumulative_o2_mg'])} |"
        )
    lines.extend(
        [
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
    limiting = summary["limiting_case"]
    diagnostic = limiting["conductance_reconciliation_diagnostic"]
    lines.extend(
        [
            "",
            "## Apparatus pressure limiting case",
            "",
            f"- Case: `{limiting['label']}`; bare pump speed "
            f"{limiting['pump_speed_m3_h']:.6g} m³/h; unrestricted line; "
            f"computed chamber-pressure records are in the paired table.",
            f"- Conductance that would reconcile to "
            f"{diagnostic['target_pressure_mbar']:.6g} mbar: "
            f"{diagnostic['line_conductance_m3_s']:.6g} m³/s. This is a "
            "diagnostic only and is not used in either scored run.",
        ]
    )
    lines.extend(["", "## O2 residuals and chamber pressure by mode", ""])
    lines.extend(
        [
            "| t (min) | Observable | Published | Imposed pressure (mbar) | "
            "Imposed simulated | Imposed residual | Limiting pressure (mbar) | "
            "Limiting simulated | Limiting residual |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    paired_records: dict[tuple[str, float], dict[str, Any]] = {}
    for record in summary["records"]:
        if record.get("observable") not in {
            "robinot_o2_ppm_time_series",
            "robinot_cumulative_o2_mass_time_series",
        } or not isinstance(record.get("t"), (int, float)):
            continue
        key = (str(record["observable"]), float(record["t"]))
        pair = paired_records.setdefault(
            key,
            {"published": record.get("published"), "units": record.get("units")},
        )
        mode_key = "imposed" if record.get("mode") == DRIVER_IMPOSED else "limiting"
        pair[f"{mode_key}_simulated"] = record.get("simulated")
        pair[f"{mode_key}_residual"] = record.get("residual")
        pair[f"{mode_key}_pressure_mbar"] = record.get("chamber_pressure_mbar")

    for (observable, time_min), pair in sorted(paired_records.items()):
        lines.append(
            "| {time} | {observable} ({units}) | {published} | {ip} | {isv} | "
            "{ir} | {lp} | {lsv} | {lr} |".format(
                time=f"{time_min:.6g}",
                observable=observable,
                units=pair.get("units", ""),
                published=format_value(pair.get("published")),
                ip=format_value(pair.get("imposed_pressure_mbar")),
                isv=format_value(pair.get("imposed_simulated")),
                ir=format_value(pair.get("imposed_residual")),
                lp=format_value(pair.get("limiting_pressure_mbar")),
                lsv=format_value(pair.get("limiting_simulated")),
                lr=format_value(pair.get("limiting_residual")),
            )
        )
    lines.extend(["", "## Melt-temperature prediction and pyrometer observation", ""])
    lines.extend(
        [
            "| Mode | Predicted peak (C) | Pyrometer observation (C) | Qualifier | "
            "Comparison status | Source locator | Flags |",
            "|---|---:|---:|---|---|---|---|",
        ]
    )
    for record in summary["records"]:
        if record.get("observable") == "robinot_pyrometer_peak_temperature_C":
            lines.append(
                "| {mode} | {predicted} | {observed} | {qualifier} | {status} | "
                "{locator} | {flags} |".format(
                    mode=record["mode"],
                    predicted=format_value(record.get("simulated")),
                    observed=format_value(record.get("published")),
                    qualifier=record.get("published_qualifier") or "-",
                    status=record.get("comparison_status", "typed_unavailable"),
                    locator=(
                        "-"
                        if record.get("source_locator") is None
                        else json.dumps(record["source_locator"], sort_keys=True)
                    ),
                    flags=", ".join(record.get("flags") or ()),
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
    observation_sidecar = ROOT / str(
        spec.document["comparison_contract"]["observation_sidecar_path"]
    )
    observations = load_vacuum_pyrolysis_observations(observation_sidecar)
    pyrometer_observation, pyrometer_unavailable_reason = (
        _reported_pyrometer_observation(spec)
    )
    imposed_run = _run_case(
        spec=spec,
        max_steps=max_steps,
        variant=DRIVER_IMPOSED,
    )
    limiting_mode = "limiting_case_unrestricted_conductance"
    limiting_run = _run_case(
        spec=spec,
        max_steps=max_steps,
        pressure_driver=DRIVER_APPARATUS,
        variant=limiting_mode,
    )
    records = _compare_trajectory(
        spec=spec,
        corpus=corpus,
        run_data=imposed_run,
        mode=DRIVER_IMPOSED,
    )
    records.extend(
        _compare_trajectory(
            spec=spec,
            corpus=corpus,
            run_data=limiting_run,
            mode=limiting_mode,
        )
    )
    records.extend(_temperature_records(imposed_run, mode=DRIVER_IMPOSED))
    records.extend(_temperature_records(limiting_run, mode=limiting_mode))
    records.append(
        _pyrometer_record(
            imposed_run,
            observation=pyrometer_observation,
            unavailable_reason=pyrometer_unavailable_reason,
            mode=DRIVER_IMPOSED,
        )
    )
    records.append(
        _pyrometer_record(
            limiting_run,
            observation=pyrometer_observation,
            unavailable_reason=pyrometer_unavailable_reason,
            mode=limiting_mode,
        )
    )
    records.append(
        _mass_integral_record(
            spec,
            imposed_run,
            observations,
            mode=DRIVER_IMPOSED,
        )
    )
    records.append(
        _mass_integral_record(
            spec,
            limiting_run,
            observations,
            mode=limiting_mode,
        )
    )

    sensitivity_runs: dict[str, dict[str, Any]] = {"imposed_hot_upper_default": imposed_run}
    sample_geometry = spec.lab_geometry["sample"]
    pellet_lengths = declared_benchtop_lengths_m(sample_geometry)
    pellet_volume_m3 = _cylinder_volume_m3(
        float(pellet_lengths["pellet_diameter_m"]),
        float(pellet_lengths["pellet_height_m"]),
    )
    molten_bead_area_m2 = _sphere_surface_area_m2_from_volume(pellet_volume_m3)
    if include_sensitivity:
        sensitivity_runs["imposed_full_side_default"] = _run_case(
            spec=spec,
            max_steps=max_steps,
            face_set=FACE_FULL_SIDE,
            variant="imposed_full_side_default",
        )
        sensitivity_runs["imposed_hot_upper_alpha_0.669"] = _run_case(
            spec=spec,
            max_steps=max_steps,
            solar_absorptance=GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_LOW,
            variant="imposed_hot_upper_alpha_0.669",
        )
        sensitivity_runs["imposed_hot_upper_alpha_0.906"] = _run_case(
            spec=spec,
            max_steps=max_steps,
            solar_absorptance=GAIER_LARGE_PARTICLE_SOLAR_ABSORPTANCE_HIGH,
            variant="imposed_hot_upper_alpha_0.906",
        )
        sensitivity_runs[
            "imposed_molten_bead_equal_volume_sphere"
        ] = _run_case(
            spec=spec,
            max_steps=max_steps,
            exposed_melt_area_m2=molten_bead_area_m2,
            variant="imposed_molten_bead_equal_volume_sphere",
        )
    sensitivity_cases = {
        name: _sensitivity_metrics(run_data)
        for name, run_data in sensitivity_runs.items()
    }
    notices_and_flags = [
        notice
        for name, run_data in {
            **sensitivity_runs,
            limiting_mode: limiting_run,
        }.items()
        for notice in _run_notices_and_flags(name, run_data)
    ]
    melt_area_assumption = {
        "area_m2": float(sample_geometry["exposed_melt_area_m2"]),
        "basis": str(sample_geometry["exposed_melt_area_basis"]),
        "source_class": str(sample_geometry["exposed_melt_area_source_class"]),
        "source_ref": str(sample_geometry["exposed_melt_area_source_ref"]),
        "citation_id": str(sample_geometry["exposed_melt_area_citation_id"]),
        "source_locator": dict(sample_geometry["exposed_melt_area_locator"]),
        "derivation": "top face: pi*(D/2)^2, D=13 mm from section 3.2",
    }
    notices_and_flags.append(
        {
            "path": "deck.lab_geometry.sample.exposed_melt_area_m2",
            "kind": "assumption/knob_defaulted",
            "value": melt_area_assumption,
        }
    )
    if include_sensitivity:
        notices_and_flags.append(
            {
                "path": "sensitivity.imposed_molten_bead_equal_volume_sphere.exposed_melt_area_m2",
                "kind": "sensitivity_assumption",
                "value": {
                    "area_m2": molten_bead_area_m2,
                    "basis": "equal_volume_sphere_from_printed_pellet_cylinder",
                    "formula": "V=pi*(D/2)^2*H; A=4*pi*(3*V/(4*pi))^(2/3)",
                    "source_locator": dict(
                        sample_geometry["exposed_melt_area_locator"]
                    ),
                },
            }
        )
    target_pressure_mbar = float(
        spec.lab_schedule["chamber_pressure_mbar"][0]["value"]
    )
    limiting_pump_speed_m3_s = (
        _ROBINOT_LIMITING_PUMP_SPEED_M3_H / float(PROCESS_HOUR_S)
    )
    reconciliation_conductance = _line_conductance_for_target_pressure_m3_s(
        carrier_flow_nl_min=float(
            spec.lab_schedule["gas_boundary"]["imposed_flow"]["value"]
        ),
        balance_temperature_K=NORMAL_LITRE_TEMPERATURE_K,
        pump_speed_m3_s=limiting_pump_speed_m3_s,
        target_pressure_pa=target_pressure_mbar * PA_PER_MBAR,
        ultimate_pressure_pa=0.0,
    )
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
            "exposed_melt_area": melt_area_assumption,
            "process_dt_s": float(spec.process_dt_s),
            "unprinted_thermal_knobs": (
                "left unset for owner defaults and notices; exposed melt area "
                "is separately declared as an assumption"
            ),
        },
        "runs": {
            DRIVER_IMPOSED: imposed_run,
            limiting_mode: limiting_run,
        },
        "limiting_case": {
            "label": limiting_mode,
            "pressure_driver": DRIVER_APPARATUS,
            "basis": "unrestricted line gives the bare-pump steady-state limit",
            "pump_speed_m3_h": _ROBINOT_LIMITING_PUMP_SPEED_M3_H,
            "pump_speed_m3_s": limiting_pump_speed_m3_s,
            "ultimate_pressure_pa": 0.0,
            "balance_temperature_K": NORMAL_LITRE_TEMPERATURE_K,
            "conductance_path": [],
            "chamber_volume_m3": None,
            "conductance_reconciliation_diagnostic": {
                "target_pressure_mbar": target_pressure_mbar,
                "line_conductance_m3_s": reconciliation_conductance,
                "used_in_scored_run": False,
                "method_owner": "simulator.teststand",
            },
            "assumptions": [
                "zero ultimate pressure is an explicit bare-pump limit, not a copied vacuum-limit value",
                "normal-litre temperature is used for the gas balance",
                "unrestricted conductance is represented by an empty declared path",
                "steady-state pressure solve does not require chamber volume",
            ],
        },
        "records": records,
        "residual_statistics": _residual_stats(records),
        "sensitivity": {
            "cases": sensitivity_cases,
            "spread": {
                "exposed_melt_area_m2": _spread(
                    sensitivity_cases, "exposed_melt_area_m2"
                ),
                "peak_predicted_temperature_C": _spread(
                    sensitivity_cases, "peak_predicted_temperature_C"
                ),
                "last_cumulative_o2_mg": _spread(
                    sensitivity_cases, "last_cumulative_o2_mg"
                ),
            },
            "equal_volume_sphere_area_formula": (
                "V = pi*(D/2)^2*H; A_bead = 4*pi*(3*V/(4*pi))^(2/3)"
            ),
        },
        "notices_and_flags": notices_and_flags,
        "digests": {
            "recipe_sha256": content_digest(spec.document),
            "corpus_sha256": content_digest(corpus["digests"]),
            "runtime_sha256": content_digest(
                {
                    "imposed": imposed_run["result"],
                    "limiting_case": limiting_run["result"],
                    "power_temperature_records": imposed_run[
                        "power_temperature_records"
                    ],
                    "limiting_power_temperature_records": limiting_run[
                        "power_temperature_records"
                    ],
                    "imposed_teststand_pressure_records": imposed_run[
                        "teststand_pressure_records"
                    ],
                    "limiting_teststand_pressure_records": limiting_run[
                        "teststand_pressure_records"
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
    json_path, markdown_path = _write_outputs(summary, args.out)
    print(f"Wrote {json_path}")
    print(f"Wrote {markdown_path}")
    return 0


def _write_outputs(
    summary: Mapping[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "robinot_2026_reproduction.json"
    markdown_path = output_dir / "robinot_2026_reproduction.md"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown_report(summary), encoding="utf-8")
    return json_path, markdown_path


if __name__ == "__main__":
    raise SystemExit(main())
