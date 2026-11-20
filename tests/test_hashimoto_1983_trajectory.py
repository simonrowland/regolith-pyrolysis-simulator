from __future__ import annotations

import pytest

import simulator.diagnostic_helpers.hashimoto_1983 as hashimoto
from simulator.diagnostic_helpers.hashimoto_1983 import (
    DEFAULT_OBSERVATIONS_PATH,
    DEFAULT_PRESET_PATH,
    Hashimoto1983Refusal,
    load_admitted_observations,
    load_hashimoto_preset,
    require_runtime_inputs,
    score_hashimoto_1983,
)
from simulator.yaml_cache import load_cached_safe_yaml


def test_hashimoto_input_is_outside_shipped_runtime_preset_catalog() -> None:
    runtime_preset_directory = DEFAULT_PRESET_PATH.parents[2] / "presets/vacuum_pyrolysis"
    catalog_paths = set(runtime_preset_directory.glob("*.yaml"))
    assert DEFAULT_PRESET_PATH.parent != runtime_preset_directory
    assert DEFAULT_PRESET_PATH not in catalog_paths


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


def test_runtime_pressure_floor_is_reported_when_area_is_available() -> None:
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    mutated = dict(preset)
    mutated["free_evaporation_geometry"] = {
        **preset["free_evaporation_geometry"],
        "exposed_melt_area_m2": 1.0e-6,
    }
    with pytest.raises(Hashimoto1983Refusal) as exc_info:
        require_runtime_inputs(mutated)
    assert exc_info.value.reason == "runtime_pressure_below_implemented_floor"
    assert str(exc_info.value) == (
        "runtime_pressure_below_implemented_floor: "
        "0.000133322 mbar < 0.001 mbar"
    )


def test_runtime_pressure_override_cannot_bypass_printed_pressure_floor() -> None:
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    preset["free_evaporation_geometry"]["exposed_melt_area_m2"] = 1.0e-6
    preset["vacuum"]["runtime_pressure_mbar"] = 0.1

    with pytest.raises(Hashimoto1983Refusal) as exc_info:
        require_runtime_inputs(preset)
    assert exc_info.value.reason == "runtime_pressure_below_implemented_floor"


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


def test_same_count_duplicate_override_refuses_after_mutation() -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    substituted = (*observations[:-1], observations[0])
    with pytest.raises(Hashimoto1983Refusal) as exc_info:
        score_hashimoto_1983(observations=substituted)
    assert exc_info.value.reason == "duplicate_admitted_observation"


def test_loader_refuses_non_wt_residue_basis_after_mutation(monkeypatch) -> None:
    document = load_cached_safe_yaml(
        DEFAULT_OBSERVATIONS_PATH.read_text(encoding="utf-8")
    )
    first_admitted = next(
        row for row in document["observations"]
        if row["identity"]["quantity"].get("value") == "residue_component_composition"
        and row["admission"]["status"] == "admitted"
    )
    first_admitted["identity"]["subtype"]["value"] = "oxide_mole_fraction"
    first_admitted["value"]["point"] = "0.0357"
    monkeypatch.setattr(hashimoto, "load_cached_safe_yaml", lambda _raw: document)
    with pytest.raises(Hashimoto1983Refusal) as exc_info:
        load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    assert exc_info.value.reason == "unsupported_residue_subtype"


def test_loader_refuses_non_table3_row_after_mutation(monkeypatch) -> None:
    document = load_cached_safe_yaml(
        DEFAULT_OBSERVATIONS_PATH.read_text(encoding="utf-8")
    )
    first_admitted = next(
        row for row in document["observations"]
        if row["identity"]["quantity"].get("value") == "residue_component_composition"
        and row["admission"]["status"] == "admitted"
    )
    first_admitted["locator"]["table"] = "2"
    monkeypatch.setattr(hashimoto, "load_cached_safe_yaml", lambda _raw: document)
    with pytest.raises(Hashimoto1983Refusal) as exc_info:
        load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    assert exc_info.value.reason == "unexpected_residue_table"


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


@pytest.mark.parametrize("invalid_output", [-1.0, 101.0])
def test_invalid_runtime_output_has_no_numeric_prediction_or_residual(
    invalid_output: float,
) -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    first = observations[0]
    runtime = {first.experiment_id: {first.oxide: invalid_output}}
    records = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=runtime,
    )
    assert records[0].refusal_reason == "invalid_runtime_output"
    assert records[0].sim_wt_pct is None
    assert records[0].residual is None
