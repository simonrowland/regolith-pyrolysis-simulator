from __future__ import annotations

import copy
from pathlib import Path

import yaml

from simulator.mre_reproduction import load_mre_reproduction_program


REPO_ROOT = Path(__file__).resolve().parent.parent
PRESET_PATH = REPO_ROOT / "data/presets/mre/yu_2025_hollow_anode.yaml"
SIDECAR_PATH = REPO_ROOT / "data/literature/mre_measurements.yaml"
DURATIONS_H = {
    "one_hour": 1.0,
    "three_hour": 3.0,
    "twelve_hour": 12.0,
}


def source_documents() -> tuple[dict, dict]:
    return (
        yaml.safe_load(PRESET_PATH.read_text(encoding="utf-8")),
        yaml.safe_load(SIDECAR_PATH.read_text(encoding="utf-8")),
    )


def synthetic_voltage_documents() -> tuple[dict, dict]:
    preset, observations = source_documents()
    observations = copy.deepcopy(observations)
    trajectory = observations["measurements"][
        "yu_2025_hollow_anode_measurements"
    ]["control_trajectories"]["yu_figure_2b_cell_potential"]
    for case_id, duration_h in DURATIONS_H.items():
        trajectory["cases"][case_id]["points"] = [
            {
                "time_h": 0.0,
                "voltage_V": 0.8,
                "unit": "V",
                "status": "published_digitized",
                "digitization_uncertainty_V": 0.01,
                "source_locator": {
                    "fixture": "synthetic_constant_voltage_for_execution_tests",
                    "case_id": case_id,
                    "point": "start",
                },
            },
            {
                "time_h": duration_h,
                "voltage_V": 0.8,
                "unit": "V",
                "status": "published_digitized",
                "digitization_uncertainty_V": 0.01,
                "source_locator": {
                    "fixture": "synthetic_constant_voltage_for_execution_tests",
                    "case_id": case_id,
                    "point": "end",
                },
            },
        ]
    return preset, observations


def synthetic_program(case_id: str, *, max_interval_min: float = 5.0):
    preset, observations = synthetic_voltage_documents()
    preset["sampling"]["max_interval_min"]["value"] = float(max_interval_min)
    return load_mre_reproduction_program(preset, observations, case_id)
