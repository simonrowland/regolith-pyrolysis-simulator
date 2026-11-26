from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

import simulator.battery.diagnostics.hashimoto_1983 as hashimoto
from simulator.battery.default_policy import (
    load_policy_table,
)
from simulator.battery.enums import NoticeKind
from simulator.battery.diagnostics.hashimoto_1983 import (
    DEFAULT_OBSERVATIONS_PATH,
    DEFAULT_PRESET_PATH,
    Hashimoto1983Refusal,
    load_admitted_observations,
    load_hashimoto_preset,
    require_runtime_inputs,
    score_hashimoto_1983,
)
from simulator.equipment import EquipmentDesigner
from simulator.yaml_cache import load_cached_safe_yaml


def test_hashimoto_input_is_outside_shipped_runtime_preset_catalog() -> None:
    runtime_preset_directory = DEFAULT_PRESET_PATH.parents[2] / "presets/vacuum_pyrolysis"
    catalog_paths = set(runtime_preset_directory.glob("*.yaml"))
    assert DEFAULT_PRESET_PATH.parent != runtime_preset_directory
    assert DEFAULT_PRESET_PATH not in catalog_paths


def test_unprinted_area_uses_runtime_policy_and_pressure_stays_printed() -> None:
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    assert preset["starting_composition_wt_pct"] == {
        "SiO2": 35.43,
        "Al2O3": 3.16,
        "FeO": 35.04,
        "MgO": 23.84,
        "CaO": 2.53,
    }
    assert preset["vacuum"]["value"] == 1.0e-4
    assert preset["vacuum"]["units"] == "Torr"
    assert "runtime_pressure_mbar" not in preset["vacuum"]
    assert preset["isothermal_holds"]["temperatures_C"] == [1700, 1800, 1900, 2000]
    assert preset["free_evaporation_geometry"]["exposed_melt_area_m2"] is None
    assert preset["vacuum"]["value"] == 1.0e-4
    assert preset["vacuum"]["units"] == "Torr"
    _assert_schedule_pressure_notice(require_runtime_inputs(preset))


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
    notice = require_runtime_inputs(mutated)
    _assert_schedule_pressure_notice(notice)
    assert mutated["vacuum"]["value"] == 1.0e-4
    assert mutated["vacuum"]["units"] == "Torr"


def test_runtime_pressure_floor_uses_lab_schedule_owner(monkeypatch) -> None:
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    preset["free_evaporation_geometry"]["exposed_melt_area_m2"] = 1.0e-6
    assert preset["vacuum"]["value"] == 1.0e-4
    assert preset["vacuum"]["units"] == "Torr"
    observed: list[float] = []

    expected_notice = {
        "kind": "lab_schedule_pressure_domain_notice",
        "reason": "lab_schedule_pressure_below_implemented_floor",
        "authority": "extrapolated",
        "pressure_mbar": 0.00013332236842105264,
        "certified_band": {"minimum_pressure_mbar": 0.001},
    }

    def domain_notice(value: float, *, t_h: float | None = None) -> dict[str, str]:
        observed.append(value)
        assert t_h is None
        return expected_notice

    monkeypatch.setattr(hashimoto, "_pressure_domain_notice_mbar", domain_notice)
    assert require_runtime_inputs(preset) == expected_notice
    assert observed == pytest.approx(
        [0.00013332236842105264],
        rel=1.0e-15,
        abs=0.0,
    )


def test_runtime_pressure_override_cannot_bypass_printed_pressure_floor() -> None:
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    preset["free_evaporation_geometry"]["exposed_melt_area_m2"] = 1.0e-6
    preset["vacuum"]["runtime_pressure_mbar"] = 0.1

    _assert_schedule_pressure_notice(require_runtime_inputs(preset))
    assert preset["vacuum"]["value"] == 1.0e-4
    assert preset["vacuum"]["units"] == "Torr"


def test_printed_pressure_value_crosses_schedule_floor() -> None:
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    preset["free_evaporation_geometry"]["exposed_melt_area_m2"] = 1.0e-6

    preset["vacuum"]["value"] = 1.0e-3
    assert require_runtime_inputs(preset) is None

    preset["vacuum"]["value"] = 1.0e-4
    _assert_schedule_pressure_notice(require_runtime_inputs(preset))
    assert preset["vacuum"]["value"] == 1.0e-4
    assert preset["vacuum"]["units"] == "Torr"


def test_admitted_rows_have_pinned_record_count_and_skip_superseded_rows() -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    preset = load_hashimoto_preset(DEFAULT_PRESET_PATH)
    assert len(observations) == 120
    assert len({row.observation_id for row in observations}) == 120
    assert all(row.source_id == "kems-015-hashimoto-1983" for row in observations)
    assert preset["vacuum"]["value"] == 1.0e-4
    assert preset["vacuum"]["units"] == "Torr"
    records = score_hashimoto_1983(preset=preset, observations=observations)
    assert len(records) == 120
    assert all(
        record.refusal_reason == "missing_runtime_output"
        for record in records
    )
    for record in records:
        _assert_schedule_pressure_notice(record.pressure_domain_notice)
        assert (
            record.pressure_domain_notice["reason"] in record.notice_set
        )
        assert (
            record.pressure_domain_notice
            in record.as_dict()["notice_details"]
        )

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
    runtime = _runtime_outputs(observations)
    records = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=runtime,
    )
    assert len(records) == 120
    assert all(record.sim_wt_pct == record.published_wt_pct for record in records)
    assert all(record.residual == 0.0 for record in records)
    _assert_dp007_residuals_are_flagged(records)
    for record in records:
        _assert_schedule_pressure_notice(record.pressure_domain_notice)
        assert (
            record.pressure_domain_notice["reason"] in record.notice_set
        )
        assert (
            record.pressure_domain_notice
            in record.as_dict()["notice_details"]
        )
    notice_payload = next(
        notice
        for notice in records[0].as_dict()["notice_details"]
        if notice.get("kind") == NoticeKind.KNOB_DEFAULTED.value
    )
    assert notice_payload["kind"] == NoticeKind.KNOB_DEFAULTED.value
    assert notice_payload["reason"] == "exposed_melt_area_not_printed"
    assert notice_payload["policy"] == {
        "id": "DP-007",
        "version": 1,
        "status": "canonical",
        "value": str(
            EquipmentDesigner().size_crucible(0.0001).melt_surface_area_m2
        ),
        "units": "m²",
    }

    changed_runtime = {key: dict(value) for key, value in runtime.items()}
    first = observations[0]
    changed_runtime[first.experiment_id][first.oxide] += 1.0
    changed = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=changed_runtime,
    )
    assert changed[0].residual == pytest.approx(1.0)


def test_default_flag_mutation_fails_the_clean_headline_invariant() -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    records = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=_runtime_outputs(observations),
    )
    assert records[0].policy_notice is not None
    mutant = replace(
        records[0],
        notice_set=records[0].notice_set - {NoticeKind.KNOB_DEFAULTED.value},
        flagged_strata=(
            records[0].flagged_strata - {NoticeKind.KNOB_DEFAULTED.value}
        ),
    )
    assert mutant.clean_measured_headline_eligible
    with pytest.raises(AssertionError):
        _assert_dp007_residuals_are_flagged((mutant,))


def test_runtime_area_default_without_policy_metadata_cannot_enter_headline() -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    runtime = _runtime_outputs(observations)
    first = observations[0]
    runtime[first.experiment_id]["run_metadata"] = {"runtime_notices": []}

    records = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=runtime,
    )

    assert records[0].refusal_reason == "runtime_area_default_notice_missing"
    assert records[0].residual is None
    assert not records[0].clean_measured_headline_eligible
    _assert_schedule_pressure_notice(records[0].pressure_domain_notice)


def test_missing_runtime_output_refuses_for_each_observation() -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    runtime = _runtime_outputs(observations)
    for run_output in runtime.values():
        for oxide in hashimoto.REQUIRED_STARTING_OXIDES:
            run_output.pop(oxide, None)
    records = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=runtime,
    )
    assert len(records) == 120
    assert all(record.sim_wt_pct is None for record in records)
    assert all(record.residual is None for record in records)
    assert all(record.refusal_reason == "missing_runtime_output" for record in records)
    assert all(
        record.pressure_domain_notice is not None
        for record in records
    )


@pytest.mark.parametrize("invalid_output", [-1.0, 101.0])
def test_invalid_runtime_output_has_no_numeric_prediction_or_residual(
    invalid_output: float,
) -> None:
    observations = load_admitted_observations(DEFAULT_OBSERVATIONS_PATH)
    first = observations[0]
    runtime = _runtime_outputs(observations)
    runtime[first.experiment_id][first.oxide] = invalid_output
    records = score_hashimoto_1983(
        observations=observations,
        runtime_outputs=runtime,
    )
    assert records[0].refusal_reason == "invalid_runtime_output"
    assert records[0].sim_wt_pct is None
    assert records[0].residual is None


def _runtime_outputs(
    observations,
    *,
    area_m2: float | None = None,
) -> dict[str, dict[str, Any]]:
    if area_m2 is None:
        # Hashimoto reports a 100.0 mg starting sample (page 115).
        area_m2 = EquipmentDesigner().size_crucible(0.0001).melt_surface_area_m2
    entry = load_policy_table()["DP-007"]
    policy = {
        "id": entry["id"],
        "version": entry["version"],
        "status": entry["status"],
        "value": str(area_m2),
        "units": entry["units"],
    }
    notice = {
        "kind": entry["flag"],
        "reason": "exposed_melt_area_not_printed",
        "detail": "runtime used its size_crucible face area",
        "policy": policy,
    }
    runtime: dict[str, dict[str, Any]] = {}
    for row in observations:
        output = runtime.setdefault(
            row.experiment_id,
            {"run_metadata": {"runtime_notices": [notice]}},
        )
        output[row.oxide] = row.published_wt_pct
    return runtime


def _assert_dp007_residuals_are_flagged(records) -> None:
    for record in records:
        assert record.residual is not None
        assert record.policy_notice is not None
        assert record.policy_notice.kind is NoticeKind.KNOB_DEFAULTED
        assert record.policy_notice.policy is not None
        assert record.policy_notice.policy.id == "DP-007"
        assert NoticeKind.KNOB_DEFAULTED.value in record.notice_set
        assert NoticeKind.KNOB_DEFAULTED.value in record.flagged_strata
        assert not record.clean_measured_headline_eligible


def _assert_schedule_pressure_notice(notice: Any) -> None:
    assert notice is not None
    assert notice["kind"] == "lab_schedule_pressure_domain_notice"
    assert notice["reason"] == "lab_schedule_pressure_below_implemented_floor"
    assert notice["authority"] == "extrapolated"
    assert notice["pressure_mbar"] == pytest.approx(
        0.00013332236842105264,
        rel=1.0e-15,
        abs=0.0,
    )
    assert notice["certified_band"] == {"minimum_pressure_mbar": 0.001}
