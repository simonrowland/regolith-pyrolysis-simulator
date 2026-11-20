from __future__ import annotations

import pytest

from simulator.diagnostic_helpers.hashimoto_1983 import (
    DEFAULT_OBSERVATIONS_PATH,
    DEFAULT_PRESET_PATH,
    Hashimoto1983Refusal,
    load_admitted_observations,
    load_hashimoto_preset,
    require_runtime_inputs,
    score_hashimoto_1983,
)


def test_preset_loads_printed_inputs_and_missing_area_refuses() -> None:
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    assert preset["starting_composition_wt_pct"] == {
        "SiO2": 35.43,
        "Al2O3": 3.16,
        "FeO": 35.04,
        "MgO": 23.84,
        "CaO": 2.53,
    }
    assert preset["vacuum"]["value"] == 1.0e-4
    assert preset["isothermal_holds"]["temperatures_C"] == [1700, 1800, 1900, 2000]
    assert preset["free_evaporation_geometry"]["exposed_melt_area_m2"] is None
    with pytest.raises(Hashimoto1983Refusal) as exc_info:
        require_runtime_inputs(preset)
    assert exc_info.value.reason == "missing_exposed_melt_area"


def test_missing_starting_oxide_refuses_after_mutation() -> None:
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    mutated = dict(preset)
    composition = dict(preset["starting_composition_wt_pct"])
    del composition["FeO"]
    mutated["starting_composition_wt_pct"] = composition
    with pytest.raises(Hashimoto1983Refusal) as exc_info:
        require_runtime_inputs(mutated)
    assert exc_info.value.reason == "missing_starting_oxide"


def test_admitted_rows_have_pinned_record_count_and_skip_superseded_rows() -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    assert len(observations) == 120
    assert len({row.observation_id for row in observations}) == 120
    assert all(row.source_id == "kems-015-hashimoto-1983" for row in observations)
    records = score_hashimoto_1983(observations=observations)
    assert len(records) == 120
    assert all(record.refusal_reason == "missing_exposed_melt_area" for record in records)

    changed = observations[:-1]
    with pytest.raises(Hashimoto1983Refusal) as exc_info:
        score_hashimoto_1983(observations=changed)
    assert exc_info.value.reason == "admitted_record_count_mismatch"


def test_synthetic_runtime_equal_to_printed_values_has_zero_residual() -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    runtime: dict[str, dict[str, float]] = {}
    for row in observations:
        runtime.setdefault(row.experiment_id, {})[row.oxide] = row.published_wt_pct
    records = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=runtime,
    )
    assert len(records) == 120
    assert all(record.sim_wt_pct == record.published_wt_pct for record in records)
    assert all(record.residual == 0.0 for record in records)

    changed_runtime = {key: dict(value) for key, value in runtime.items()}
    first = observations[0]
    changed_runtime[first.experiment_id][first.oxide] += 1.0
    changed = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=changed_runtime,
    )
    assert changed[0].residual == pytest.approx(1.0)


def test_missing_runtime_output_refuses_for_each_observation() -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    runtime = {row.experiment_id: {} for row in observations}
    records = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=runtime,
    )
    assert len(records) == 120
    assert all(record.sim_wt_pct is None for record in records)
    assert all(record.residual is None for record in records)
    assert all(record.refusal_reason == "missing_runtime_output" for record in records)
