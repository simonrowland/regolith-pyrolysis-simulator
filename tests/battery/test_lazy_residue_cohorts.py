from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from simulator.battery import residue


ROOT = Path(__file__).resolve().parents[2]
HASHIMOTO_EXTRACT = ROOT / "data/literature/extracts/kems-015-hashimoto-1983.yaml"
HASHIMOTO_STORE = ROOT / "data/literature/extracts-v2/kems-015-hashimoto-1983.yaml"


def _encode(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _without_cpu_timing(row: object) -> dict[str, object]:
    payload = asdict(row)
    integration = payload["provenance"]["integration"]
    integration.pop("process_cpu_seconds", None)
    integration.pop("process_cpu_seconds_by_geometry", None)
    return payload


def test_hashimoto_selected_experiments_match_full_rows_and_integrate_only_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openimcc")
    source = yaml.safe_load(HASHIMOTO_EXTRACT.read_text())
    store = yaml.safe_load(HASHIMOTO_STORE.read_text())
    measured_rows = [
        observation
        for observation in store["observations"]
        if "::hashimoto_1983_table3_residue_composition_series::"
        in observation["observation_id"]
        and observation["value"].get("kind") == "point"
    ]
    experiment_ids = sorted(
        {
            observation["experiment_id"].split("::experiment::")[-1]
            for observation in measured_rows
        }
    )
    experiments_by_id = {
        experiment["experiment_id"]: experiment
        for experiment in source["experiments"]
    }
    experiments = [experiments_by_id[experiment_id] for experiment_id in experiment_ids]
    assert len(experiments) == 24
    preform_dimensions_mm = next(
        item["values"]["starting_preform_mm"]
        for item in source["metadata"]
        if item["observation_id"]
        == "hashimoto_1983_fe_fcmas_free_evap_geometry_quoted"
    )
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    integrated_conditions: list[tuple[float, float]] = []

    def integrate_at_steps(
        inventory,
        _channels,
        _pressure_model,
        *,
        temperature_K,
        duration_s,
        steps,
        geometry_policy_id,
        initial_area_m2,
    ):
        integrated_conditions.append((temperature_K, duration_s))
        values = residue._hashimoto_project_oxide_wt_pct(inventory)
        shift = 0.8 / steps
        values["FeO"] += shift
        values["MgO"] -= shift
        return (
            values,
            (0.1,),
            (),
            None,
            {"Fe": 0.0, "O": 0.0},
            {
                "steps_at_floor": 0,
                "first_step": None,
                "fraction_of_time_at_floor": 0.0,
                "time_at_floor_s": 0.0,
                "pO2_bar": None,
            },
        )

    monkeypatch.setattr(residue, "_hashimoto_integrate_geometry", integrate_at_steps)
    target = experiments[-1]
    target_id = target["experiment_id"]
    target_conditions = (
        residue._hashimoto_point_value(target["conditions"]["temperature_K"]),
        residue._hashimoto_point_value(
            target["thermal_schedule"]["total_duration_s"]
        ),
    )
    full = residue._predict_hashimoto_residue_cohort(
        experiments,
        catalog,
        preform_dimensions_mm=preform_dimensions_mm,
        code_revision="lazy-selection-test",
    )
    full_target = [row for row in full if row.experiment_id == target_id]
    assert len(full_target) == 2
    integrated_conditions.clear()

    selected = residue._predict_hashimoto_residue_cohort(
        experiments,
        catalog,
        preform_dimensions_mm=preform_dimensions_mm,
        code_revision="lazy-selection-test",
        selected_experiment_ids=frozenset({target_id}),
    )

    # Measured CPU timings are nondeterministic between runs; their follow-up
    # is tracked in score-overhead-11111640. Keep every other byte pinned.
    selected_payloads = [_without_cpu_timing(row) for row in selected]
    full_payloads = [_without_cpu_timing(row) for row in full_target]
    assert selected_payloads == full_payloads
    assert _encode(selected_payloads) == _encode(full_payloads)
    assert {row.experiment_id for row in selected} == {target_id}
    assert integrated_conditions
    assert set(integrated_conditions) == {target_conditions}


def test_sossi_selected_experiments_match_full_rows_and_integrate_only_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = yaml.safe_load(
        (ROOT / "data/literature/extracts/kems-012-sossi-2019.yaml").read_text()
    )
    experiments = source["experiments"][:2]
    experiment_ids = {experiment["experiment_id"] for experiment in experiments}
    starting_trace = {
        element: source["species"][element]["observations"][0]["values"][
            "starting_measured_ppm"
        ]
        for element in ("Mn", "Ti")
    }
    starting_trace_by_experiment = {
        experiment_id: starting_trace for experiment_id in experiment_ids
    }
    buffered_fO2_by_experiment = {
        experiment_id: -10.0 for experiment_id in experiment_ids
    }
    catalog = yaml.safe_load((ROOT / "data/vapor_pressures.yaml").read_text())
    integrated_experiments: list[str] = []
    active_experiment_id = ""
    original_initial_inventory = residue._sossi_initial_inventory

    def track_initial_inventory(experiment, traces):
        nonlocal active_experiment_id
        active_experiment_id = experiment["experiment_id"]
        return original_initial_inventory(experiment, traces)

    def no_evaporation(inventory, *_args, **_kwargs):
        integrated_experiments.append(active_experiment_id)
        return SimpleNamespace(
            residue_mol=dict(inventory), buffer_oxygen_exchange_mol=0.0
        )

    monkeypatch.setattr(residue, "_sossi_initial_inventory", track_initial_inventory)
    monkeypatch.setattr(residue, "integrate_residue_inventory", no_evaporation)
    full = residue._predict_sossi_residue_cohort(
        experiments,
        catalog,
        starting_trace_ppm_by_experiment=starting_trace_by_experiment,
        buffered_fO2_log_by_experiment=buffered_fO2_by_experiment,
        code_revision="lazy-selection-test",
        engine="internal-analytical",
    )
    assert set(integrated_experiments) == experiment_ids
    target_id = experiments[0]["experiment_id"]
    full_target = next(row for row in full if row.experiment_id == target_id)
    integrated_experiments.clear()

    selected = residue._predict_sossi_residue_cohort(
        experiments,
        catalog,
        starting_trace_ppm_by_experiment=starting_trace_by_experiment,
        buffered_fO2_log_by_experiment=buffered_fO2_by_experiment,
        code_revision="lazy-selection-test",
        engine="internal-analytical",
        selected_experiment_ids=frozenset({target_id}),
    )

    assert len(selected) == 1
    assert selected[0].experiment_id == target_id
    assert _encode(asdict(selected[0])) == _encode(asdict(full_target))
    assert integrated_experiments
    assert set(integrated_experiments) == {target_id}
