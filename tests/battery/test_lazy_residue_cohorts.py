from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
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


def test_residue_cohort_caches_are_scoped_by_selected_experiments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery import score
    from simulator.battery.enums import Engine, EvidenceClass, ExecutionState, Phase, Quantity
    from simulator.battery.identity import Identity
    from simulator.battery.records import Species
    from . import factories as F

    hashimoto_source = score._HASHIMOTO_SOURCE_ID
    sossi_source = score._SOSSI_SOURCE_ID
    hashimoto_experiments = {
        f"{hashimoto_source}::experiment::run-{index:02d}": F.tabulation_experiment(
            experiment_id=f"{hashimoto_source}::experiment::run-{index:02d}"
        )
        for index in range(24)
    }
    hashimoto_observations = {
        f"{hashimoto_source}::residue::run-{index:02d}": F.observation(
            f"{hashimoto_source}::residue::run-{index:02d}",
            experiment_id,
            Identity(
                Quantity.RESIDUE_COMPONENT_COMPOSITION,
                Species("FeO", Phase.L),
            ),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=hashimoto_source,
        )
        for index, experiment_id in enumerate(hashimoto_experiments)
    }
    hashimoto_context = score.ScoreContext(
        works={},
        experiments=hashimoto_experiments,
        observations=hashimoto_observations,
        hostname="test",
    )
    sossi_short_ids = tuple(f"run-{index:02d}" for index in range(43))
    sossi_experiments = [
        {"experiment_id": short_id} for short_id in sossi_short_ids
    ]
    sossi_observations = {
        f"{sossi_source}::residue::{short_id}": F.observation(
            f"{sossi_source}::residue::{short_id}",
            f"{sossi_source}::experiment::{short_id}",
            Identity(
                Quantity.RESIDUE_COMPONENT_COMPOSITION,
                Species("Mn", Phase.L),
            ),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=sossi_source,
        )
        for short_id in sossi_short_ids
    }
    sossi_context = score.ScoreContext(
        works={}, experiments={}, observations=sossi_observations, hostname="test"
    )

    def load_yaml(path):
        if hashimoto_source in str(path):
            return {
                "metadata": [
                    {
                        "observation_id": "hashimoto_1983_fe_fcmas_free_evap_geometry_quoted",
                        "values": {"starting_preform_mm": {"diameter": 1.0}},
                    }
                ]
            }
        if sossi_source in str(path):
            return {"experiments": sossi_experiments}
        return {}

    monkeypatch.setattr(score, "load_yaml", load_yaml)
    monkeypatch.setattr(
        score.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="test-revision\n"),
    )
    monkeypatch.setattr(score, "openimcc_pack_coefficient_sources", lambda *_args: ())
    monkeypatch.setattr(score, "openimcc_engine_identity_sources", lambda *_args: ())

    hashimoto_calls: list[frozenset[str] | None] = []

    def hashimoto_cohort(
        experiments, _catalog, *, selected_experiment_ids=None, **_kwargs
    ):
        hashimoto_calls.append(selected_experiment_ids)
        experiment_ids = (
            selected_experiment_ids
            if selected_experiment_ids is not None
            else {experiment["experiment_id"] for experiment in experiments}
        )
        return tuple(
            SimpleNamespace(
                experiment_id=experiment_id,
                alpha_arm=alpha_arm,
                primary_geometry_policy_id="sphere_shrinking",
                primary_oxide_wt_pct={"FeO": 1.0},
                geometry_oxide_wt_pct={"sphere_shrinking": {"FeO": 1.0}},
                sensitivity_band_wt_pct={"FeO": (1.0, 1.0)},
                provenance={
                    "code_revision": "test-revision",
                    "integration": {},
                    "law_disclosures": [],
                },
            )
            for experiment_id in experiment_ids
            for alpha_arm in (
                "alpha_common_unity_sensitivity",
                "alpha_runtime_catalog",
            )
        )

    monkeypatch.setattr(residue, "_predict_hashimoto_residue_cohort", hashimoto_cohort)
    hashimoto_cache = {}
    hashimoto_ids = tuple(hashimoto_experiments)
    for experiment_id in hashimoto_ids[:2]:
        prediction = score._hashimoto_residue_prediction(
            hashimoto_context,
            next(
                observation
                for observation in hashimoto_observations.values()
                if observation.experiment_id == experiment_id
            ),
            Engine.INTERNAL_ANALYTICAL,
            hashimoto_cache,
            selected_experiment_ids=frozenset({experiment_id}),
        )
        assert prediction.execution.state is ExecutionState.PRODUCED, prediction.refusal_detail
    assert hashimoto_calls == [
        frozenset({hashimoto_ids[0]}),
        frozenset({hashimoto_ids[1]}),
    ]

    sossi_calls: list[frozenset[str] | None] = []

    def sossi_cohort(
        experiments, _catalog, *, selected_experiment_ids=None, **_kwargs
    ):
        sossi_calls.append(selected_experiment_ids)
        return tuple(
            SimpleNamespace(
                experiment_id=experiment["experiment_id"],
                primary_element_ppm={"Mn": 10.0},
                geometry_element_ppm={"Mn": {"sphere_constant": 10.0}},
                sensitivity_band_ppm={"Mn": (9.0, 11.0)},
                channel_missing_elements=(),
                provenance={"law_disclosures": []},
            )
            for experiment in experiments
            if selected_experiment_ids is None
            or experiment["experiment_id"] in selected_experiment_ids
        )

    monkeypatch.setattr(residue, "_predict_sossi_residue_cohort", sossi_cohort)
    sossi_cache = {}
    for short_id in sossi_short_ids[:2]:
        prediction = score._sossi_residue_prediction(
            sossi_context,
            sossi_observations[f"{sossi_source}::residue::{short_id}"],
            Engine.INTERNAL_ANALYTICAL,
            sossi_cache,
            selected_experiment_ids=frozenset({short_id}),
        )
        assert prediction.execution.state is ExecutionState.PRODUCED
    assert sossi_calls == [
        frozenset({sossi_short_ids[0]}),
        frozenset({sossi_short_ids[1]}),
    ]
