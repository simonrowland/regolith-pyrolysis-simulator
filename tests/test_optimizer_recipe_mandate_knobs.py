from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.optimize.recipe import (
    MANDATE_LEVER_ALLOWLIST,
    MANDATE_LEVER_PATHS,
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
)


SETPOINTS_PATH = Path(__file__).resolve().parents[1] / "data" / "setpoints.yaml"


def _path(dotted: str) -> tuple[str, ...]:
    return tuple(dotted.split("."))


def _lookup(root: dict, path: tuple[str, ...]):
    node = root
    for segment in path:
        node = node[segment]
    return node


def _sample_value(spec):
    if spec.kind == "int":
        assert spec.low is not None
        assert spec.high is not None
        return int((spec.low + spec.high) // 2)
    if spec.kind == "categorical":
        assert spec.choices
        return spec.choices[0]
    assert spec.low is not None
    assert spec.high is not None
    midpoint = (spec.low + spec.high) / 2.0
    if spec.path in RecipeSchema.NUMERIC_PAIR_VALUE_PATHS:
        return [spec.low, spec.high]
    return midpoint


def test_mandate_lever_allowlist_is_default_schema_subset() -> None:
    schema = RecipeSchema()
    schema_paths = {spec.path for spec in schema.allowlist}
    mandate_paths = {spec.path for spec in MANDATE_LEVER_ALLOWLIST}

    assert mandate_paths == MANDATE_LEVER_PATHS
    assert mandate_paths <= schema_paths
    assert "allowlist-v3" == schema.allowlist_version


def test_mandate_lever_paths_are_tunable_and_real_setpoint_paths() -> None:
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    schema = RecipeSchema()

    required_examples = {
        _path("campaigns.C0b_p_cleanup.pO2_mbar_default"),
        _path("campaigns.C2A_continuous.p_total_mbar_default"),
        _path("campaigns.C2A_staged.na_shuttle_stage.ramp_rate_C_per_hr"),
        _path("campaigns.C2A_staged.na_shuttle_stage.duration_h"),
        _path("campaigns.C3.endpoint.hold_time_min"),
        _path("campaigns.C3.alkali_dosing.Na_kg"),
        _path("campaigns.C3.alkali_dosing.K_kg"),
        _path("overhead_headspace.temperature_offset_K"),
        _path(
            "overhead_headspace.pipe_segment_temperatures_C.segments."
            "stage_4_to_stage_5.default_C"
        ),
    }
    mandate_paths = {spec.path for spec in MANDATE_LEVER_ALLOWLIST}
    assert required_examples <= mandate_paths

    for spec in MANDATE_LEVER_ALLOWLIST:
        _lookup(setpoints, spec.path)
        patch = RecipePatch({spec.path: _sample_value(spec)})
        assert patch.validated(schema).values[spec.path] == _sample_value(spec)


def test_wall_temperature_knobs_render_to_setpoints_patch() -> None:
    schema = RecipeSchema()
    wall_offset = _path("overhead_headspace.temperature_offset_K")
    cold_segment = _path(
        "overhead_headspace.pipe_segment_temperatures_C.segments."
        "stage_4_to_stage_5.default_C"
    )
    patch = RecipePatch({wall_offset: -75.0, cold_segment: 425.0})

    nested = schema.to_setpoints_patch(patch)

    assert nested["overhead_headspace"]["temperature_offset_K"] == -75.0
    assert (
        nested["overhead_headspace"]["pipe_segment_temperatures_C"]["segments"][
            "stage_4_to_stage_5"
        ]["default_C"]
        == 425.0
    )
    assert patch.validated(schema).recipe_id() != RecipePatch({}).recipe_id(schema)


def test_c3_alkali_dosing_knobs_render_to_setpoints_patch() -> None:
    schema = RecipeSchema()
    na_dose = _path("campaigns.C3.alkali_dosing.Na_kg")
    k_dose = _path("campaigns.C3.alkali_dosing.K_kg")
    patch = RecipePatch({na_dose: 12.0, k_dose: 4.0})

    nested = schema.to_setpoints_patch(patch)

    assert nested["campaigns"]["C3"]["alkali_dosing"]["Na_kg"] == pytest.approx(12.0)
    assert nested["campaigns"]["C3"]["alkali_dosing"]["K_kg"] == pytest.approx(4.0)
    assert patch.validated(schema).recipe_id() != RecipePatch({}).recipe_id(schema)


def test_pending_dose_paths_and_forbidden_constants_stay_blocked() -> None:
    pending_backend_paths = (
        _path("campaigns.C3.K_phase.K_per_cycle_kg"),
        _path("campaigns.C3.Na_phase.Na_total_kg"),
        _path("campaigns.C2A_staged.na_shuttle_stage.recommended_Na_kg"),
    )
    forbidden_paths = (
        _path("constants.FARADAY"),
        _path("campaigns.C3.constants.evaporation_alpha"),
        _path("campaigns.C3.products.Na_kg"),
        _path("mass_balance.gap_pct"),
        _path("chemistry_kernel.allow_fallback_vapor"),
    )

    for path in pending_backend_paths:
        with pytest.raises(RecipeValidationError, match="unknown recipe path"):
            RecipePatch({path: 1.0}).validated()

    for path in forbidden_paths:
        with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
            RecipePatch({path: 1.0}).validated()
