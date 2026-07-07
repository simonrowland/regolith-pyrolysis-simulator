from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.core import CampaignPhase
from simulator.optimize.recipe import (
    C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH,
    FURNACE_MAX_T_C_PATH,
    MANDATE_LEVER_ALLOWLIST,
    MANDATE_LEVER_PATHS,
    OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C,
    OVERHEAD_HEADSPACE_OFFSET_MAX_K,
    OVERHEAD_HEADSPACE_OFFSET_MIN_K,
    OVERHEAD_HOT_WALL_MAX_C,
    OVERHEAD_HOT_WALL_MIN_C,
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
)
from simulator.state import BatchRecord, CondensationTrain, EvaporationFlux, MeltState


SETPOINTS_PATH = Path(__file__).resolve().parents[1] / "data" / "setpoints.yaml"


def _path(dotted: str) -> tuple[str, ...]:
    return tuple(dotted.split("."))


C4_RUNTIME_ONLY_PATHS = {_path("campaigns.C4.hold_temp_C")}
C2A_STAGED_STAGE_GAS_PATHS = {
    _path(f"campaigns.C2A_staged.stages.{stage}.{field}")
    for stage in (
        "alkali_early_fe",
        "sio_window",
        "fe_hot_hold",
        "cool_for_na_shuttle",
    )
    for field in ("pO2_mbar", "p_total_mbar", "gas_cover_mode")
}


def _lookup(root: dict, path: tuple[str, ...]):
    node = root
    for segment in path:
        if isinstance(node, list):
            node = next(item for item in node if item["name"] == segment)
        else:
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
    assert "allowlist-v11" == schema.allowlist_version

    # P1 #1: campaigns.C2A_staged.stages.fe_hot_hold.target_C is a silent no-op
    # (the runtime holds fe_hot_hold at default_hold_T_C / the C4-style override,
    # never the stage target_C — see campaigns.py:_get_base_temp_target). It must
    # stay OUT of both the search allowlist and the mandate-lever set so the
    # optimizer never tunes a knob that changes nothing (SC-07 inert knob).
    fe_hot_hold_target = _path(
        "campaigns.C2A_staged.stages.fe_hot_hold.target_C"
    )
    assert fe_hot_hold_target not in MANDATE_LEVER_PATHS
    assert fe_hot_hold_target not in schema_paths

    optional_ca_harvest_po2 = _path("campaigns.C4.optional_Ca_harvest.pO2_mbar")
    optional_ca_harvest_spec = schema.spec_for(optional_ca_harvest_po2)
    searchable_paths = {spec.path for spec in schema.search_allowlist}
    assert optional_ca_harvest_spec.search_enabled is False
    assert optional_ca_harvest_po2 not in searchable_paths


def test_overhead_temperature_bounds_are_hot_wall_grounded() -> None:
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    schema = RecipeSchema()
    metals_train = setpoints["condensation_train"]["metals_train"]
    hot_duct = metals_train["stage_0_hot_duct"]
    c2a_continuous = setpoints["campaigns"]["C2A_continuous"]

    assert hot_duct["temp_range_C"][0] == OVERHEAD_HOT_WALL_MIN_C
    assert hot_duct["max_service_T_C"] == OVERHEAD_HOT_WALL_MAX_C
    assert (
        OVERHEAD_HEADSPACE_OFFSET_MIN_K
        == OVERHEAD_HOT_WALL_MIN_C - c2a_continuous["temp_range_C"][1]
    )
    assert OVERHEAD_HEADSPACE_OFFSET_MAX_K == 0.0

    hot_wall_paths = [
        "overhead_headspace.liner_temperature_C.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_0_to_stage_1.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_1_to_stage_2.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_2_to_stage_3.default_C",
        "overhead_headspace.pipe_segment_temperatures_C.segments.stage_3_to_stage_4.default_C",
    ]
    for dotted in hot_wall_paths:
        spec = schema.spec_for(_path(dotted))
        assert spec.low == OVERHEAD_HOT_WALL_MIN_C
        assert spec.high == OVERHEAD_HOT_WALL_MAX_C
        assert "hot_wall_invariant" in spec.bounds_source

    offset_spec = schema.spec_for(
        _path("overhead_headspace.temperature_offset_K")
    )
    assert offset_spec.low == OVERHEAD_HEADSPACE_OFFSET_MIN_K
    assert offset_spec.high == OVERHEAD_HEADSPACE_OFFSET_MAX_K
    assert "C2A_continuous peak SiO window" in offset_spec.bounds_source

    default_train = CondensationTrain.create_default()
    stage_ranges = {
        stage.stage_number: (
            float(stage.temp_range_C[0]),
            float(stage.temp_range_C[1]),
        )
        for stage in default_train.stages
    }
    downstream_bounds = {
        "stage_4_to_stage_5": (stage_ranges[5][0], stage_ranges[4][1]),
        "stage_5_to_stage_6": (stage_ranges[6][0], stage_ranges[5][1]),
        "stage_6_to_stage_7": (stage_ranges[7][0], stage_ranges[6][1]),
    }
    assert dict(OVERHEAD_DOWNSTREAM_SEGMENT_BOUNDS_C) == downstream_bounds
    for segment_name, (low, high) in downstream_bounds.items():
        spec = schema.spec_for(
            _path(
                "overhead_headspace.pipe_segment_temperatures_C.segments."
                f"{segment_name}.default_C"
            )
        )
        assert spec.low == low
        assert spec.high == high
        assert "CondensationTrain.create_default" in spec.bounds_source


def test_mandate_lever_paths_are_tunable_and_real_setpoint_paths() -> None:
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    schema = RecipeSchema()

    required_examples = {
        _path("furnace_max_T_C"),
        _path("campaigns.C0b_p_cleanup.pO2_mbar_default"),
        _path("campaigns.C2A_continuous.p_total_mbar_default"),
        _path("campaigns.C2A_staged.na_shuttle_stage.ramp_rate_C_per_hr"),
        _path("campaigns.C2A_staged.na_shuttle_stage.duration_h"),
        _path("campaigns.C2A_staged.depletion_flux_decay_fraction"),
        _path("campaigns.C2A_staged.stages.sio_window.target_C"),
        _path("campaigns.C2A_staged.stages.sio_window.pO2_mbar"),
        _path("campaigns.C2A_staged.stages.sio_window.p_total_mbar"),
        _path("campaigns.C2A_staged.stages.sio_window.gas_cover_mode"),
        _path("campaigns.C2A_staged.stages.fe_hot_hold.duration_h"),
        _path("campaigns.C3.endpoint.hold_time_min"),
        _path("campaigns.C3.alkali_dosing.Na_kg"),
        _path("campaigns.C3.alkali_dosing.K_kg"),
        _path("campaigns.C4.hold_temp_C"),
        _path("campaigns.C5.allow_mre_voltage_cap_V"),
        _path("overhead_headspace.temperature_offset_K"),
        _path(
            "overhead_headspace.pipe_segment_temperatures_C.segments."
            "stage_4_to_stage_5.default_C"
        ),
    }
    mandate_paths = {spec.path for spec in MANDATE_LEVER_ALLOWLIST}
    assert required_examples <= mandate_paths

    # pO2 defaults are jointly constrained with their campaign's total-pressure
    # default (partial <= total, validate-don't-derive): tuning one above the
    # YAML default total requires patching the pair, so sample the pair here.
    pair_map = dict(RecipeSchema.PRESSURE_TOTAL_DEFAULT_BY_PO2_DEFAULT)
    spec_by_path = {spec.path: spec for spec in schema.allowlist}
    for spec in MANDATE_LEVER_ALLOWLIST:
        if spec.path not in C4_RUNTIME_ONLY_PATHS | C2A_STAGED_STAGE_GAS_PATHS:
            _lookup(setpoints, spec.path)
        values = {spec.path: _sample_value(spec)}
        total_path = pair_map.get(spec.path)
        if total_path is not None:
            total_spec = spec_by_path[total_path]
            values[total_path] = max(
                float(values[spec.path]), float(_sample_value(total_spec))
            )
        patch = RecipePatch(values)
        assert patch.validated(schema).values[spec.path] == _sample_value(spec)
        if spec.path in C2A_STAGED_STAGE_GAS_PATHS:
            rendered = schema.to_setpoints_patch(patch.validated(schema))
            assert _lookup(rendered, spec.path) == _sample_value(spec)


def test_c2a_staged_depletion_flux_decay_mandate_knob_is_runtime_live() -> None:
    schema = RecipeSchema()
    patch = RecipePatch({C2A_STAGED_DEPLETION_FLUX_DECAY_FRACTION_PATH: 0.25})
    nested = schema.to_setpoints_patch(patch)
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    setpoints["campaigns"]["C2A_staged"].update(nested["campaigns"]["C2A_staged"])
    manager = CampaignManager(setpoints)
    manager.configure_campaign(
        MeltState(campaign=CampaignPhase.C2A_STAGED),
        CampaignPhase.C2A_STAGED,
    )
    flux = EvaporationFlux(species_kg_hr={"Na": 10.0, "K": 8.0})
    flux.update_totals()
    assert not manager.check_endpoint(
        MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=0),
        flux,
        CondensationTrain.create_default(),
        BatchRecord(),
    )
    flux = EvaporationFlux(species_kg_hr={"Na": 2.4, "K": 1.9})
    flux.update_totals()

    assert not manager.check_endpoint(
        MeltState(campaign=CampaignPhase.C2A_STAGED, campaign_hour=1),
        flux,
        CondensationTrain.create_default(),
        BatchRecord(),
    )
    assert manager._c2a_staged_stage_idx == 1


def test_furnace_max_t_c_mandate_knob_is_runtime_live() -> None:
    schema = RecipeSchema()
    patch = RecipePatch({FURNACE_MAX_T_C_PATH: 1300.0})
    nested = schema.to_setpoints_patch(patch)
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    setpoints.update(nested)
    manager = CampaignManager(setpoints)

    target, _ = manager.get_temp_target(
        CampaignPhase.C2A,
        0,
        MeltState(campaign=CampaignPhase.C2A, temperature_C=1200.0),
    )

    assert target == pytest.approx(1300.0)
    assert patch.validated(schema).recipe_id() != RecipePatch({}).recipe_id(schema)


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
