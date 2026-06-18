from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest

from simulator.chemistry.kernel import (
    OXYGEN_SINK_CHANNEL_MODE_KEY,
    OXYGEN_SINK_CHANNEL_MODE_VALUES,
)
from simulator.optimize.recipe import (
    C5_ALLOW_MRE_VOLTAGE_CAP_PATH,
    C4_HOLD_TEMP_C_PATH,
    KnobSpec,
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
    STAGE0_CARBON_REDUCTANT_KG_PATH,
    STAGE0_REDOX_OXIDANT_KG_PATH,
)
from simulator.optimize.evalspec import EvalSpec, canonical_evalspec_json
from simulator.campaigns import CampaignManager
from simulator.core import CampaignPhase
from simulator.runner import PyrolysisRun
from simulator.session import SimSession
from simulator.state import MeltState


FEEDSTOCK = "lunar_mare_low_ti"
PO2_DEFAULT = ("campaigns", "C0b_p_cleanup", "pO2_mbar_default")
PTOTAL_DEFAULT = ("campaigns", "C0b_p_cleanup", "p_total_mbar_default")
C3_PO2_DEFAULT = ("campaigns", "C3", "pO2_mbar_default")
C3_PTOTAL_DEFAULT = ("campaigns", "C3", "p_total_mbar_default")
PRODUCT_TARGET = ("campaigns", "C0b_p_cleanup", "products", "oxygen_kg")
OXYGEN_SINK_CHANNEL_MODE = ("chemistry_kernel", OXYGEN_SINK_CHANNEL_MODE_KEY)
SETPOINTS_PATH = Path(__file__).resolve().parents[1] / "data" / "setpoints.yaml"
STAGE_SIO_TARGET = (
    "campaigns",
    "C2A_staged",
    "stages",
    "sio_window",
    "target_C",
)
STAGE_FE_DURATION = (
    "campaigns",
    "C2A_staged",
    "stages",
    "fe_hot_hold",
    "duration_h",
)
STAGE_COOL_RAMP = (
    "campaigns",
    "C2A_staged",
    "stages",
    "cool_for_na_shuttle",
    "ramp_rate_C_per_hr",
)
DATA_DIGESTS = {
    "feedstocks": "feedstocks-digest",
    "profile": "profile-digest",
    "setpoints": "setpoints-digest",
    "vapor_pressures": "vapor-pressures-digest",
}


def _lookup_setpoint(root: dict, dotted_path: str):
    node = root
    for segment in dotted_path.split("."):
        node = node[segment]
    return node


def _stage_by_name(stages: list[dict], name: str) -> dict:
    return next(stage for stage in stages if stage["name"] == name)


def test_unknown_setpoint_path_is_denied_by_default() -> None:
    patch = RecipePatch({("campaigns", "C0", "label"): "retuned"})

    with pytest.raises(RecipeValidationError, match="unknown recipe path"):
        patch.validated()


def test_forbidden_prefixes_are_hard_errors() -> None:
    forbidden = [
        ("chemistry_kernel", "allow_fallback_vapor"),
        PRODUCT_TARGET,
        ("mass_balance", "gap_pct"),
    ]

    for path in forbidden:
        with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
            RecipePatch({path: 1.0}).validated()


def test_forbidden_prefix_wins_over_overlapping_allowlist() -> None:
    schema = RecipeSchema(
        allowlist=(
            KnobSpec(
                path=PRODUCT_TARGET,
                kind="float",
                low=0.0,
                high=10.0,
                bounds_source="test",
            ),
        )
    )

    with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
        RecipePatch({PRODUCT_TARGET: 1.0}).validated(schema)


def test_bounds_and_type_checks_for_allowlisted_knob() -> None:
    RecipePatch({PO2_DEFAULT: 9.0}).validated()

    with pytest.raises(RecipeValidationError, match="above upper bound"):
        RecipePatch({PO2_DEFAULT: 30.0}).validated()

    with pytest.raises(RecipeValidationError, match="requires float value"):
        RecipePatch({PO2_DEFAULT: "9.0"}).validated()


def test_int_kind_rejects_float_and_bool() -> None:
    hold_time = ("campaigns", "C3", "endpoint", "hold_time_min")
    RecipePatch({hold_time: 30}).validated()

    with pytest.raises(RecipeValidationError, match="requires int value"):
        RecipePatch({hold_time: 30.5}).validated()

    with pytest.raises(RecipeValidationError, match="requires int value"):
        RecipePatch({hold_time: True}).validated()


def test_nested_yaml_round_trip_and_setpoints_patch_smoke() -> None:
    patch = RecipePatch(
        {
            PO2_DEFAULT: 10.0,
            PTOTAL_DEFAULT: 10.0,
            ("campaigns", "C2A_continuous", "duration_h"): [20, 24],
        }
    )

    schema = RecipeSchema()
    nested = schema.to_setpoints_patch(patch)
    loaded = yaml.safe_load(yaml.safe_dump(nested, sort_keys=True))
    loaded_patch = RecipePatch.from_nested(loaded)
    assert loaded_patch.values[PO2_DEFAULT] == pytest.approx(10.0)
    assert loaded_patch.values[PTOTAL_DEFAULT] == pytest.approx(10.0)
    assert loaded_patch.values[
        ("campaigns", "C2A_continuous", "duration_h")
    ] == [20, 24]

    run = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)
    config = run._session_config()
    assert config.setpoints["campaigns"]["C0b_p_cleanup"]["pO2_mbar_default"] == 10.0
    assert config.setpoints["campaigns"]["C0b_p_cleanup"]["p_total_mbar_default"] == 10.0
    assert config.setpoints["campaigns"]["C2A_continuous"]["duration_h"] == [
        20,
        24,
    ]


def test_c2a_staged_named_stage_knobs_render_to_real_stage_list() -> None:
    schema = RecipeSchema()
    stage_fields = {
        "alkali_early_fe": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
        "sio_window": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
        "fe_hot_hold": ("duration_h", "ramp_rate_C_per_hr"),
        "cool_for_na_shuttle": ("duration_h", "target_C", "ramp_rate_C_per_hr"),
    }
    stage_paths = {
        (
            "campaigns",
            "C2A_staged",
            "stages",
            stage,
            field,
        )
        for stage, fields in stage_fields.items()
        for field in fields
    }
    search_paths = {spec.path for spec in schema.search_allowlist}

    assert stage_paths <= search_paths
    assert (
        "campaigns",
        "C2A_staged",
        "stages",
        "fe_hot_hold",
        "target_C",
    ) not in search_paths
    patch = RecipePatch(
        {
            STAGE_SIO_TARGET: 1585.0,
            STAGE_FE_DURATION: 2,
            STAGE_COOL_RAMP: 500.0,
        }
    ).validated(schema)
    nested = schema.to_setpoints_patch(patch)
    loaded_patch = RecipePatch.from_nested(nested).validated(schema)
    stages = nested["campaigns"]["C2A_staged"]["stages"]

    assert loaded_patch.values[STAGE_SIO_TARGET] == pytest.approx(1585.0)
    assert loaded_patch.values[STAGE_FE_DURATION] == 2
    assert loaded_patch.values[STAGE_COOL_RAMP] == pytest.approx(500.0)
    assert _stage_by_name(stages, "sio_window")["target_C"] == pytest.approx(1585.0)
    assert _stage_by_name(stages, "fe_hot_hold")["duration_h"] == 2
    assert _stage_by_name(stages, "cool_for_na_shuttle")[
        "ramp_rate_C_per_hr"
    ] == pytest.approx(500.0)
    assert nested["campaigns"]["C2A_staged"]["max_hold_hr"] == 10

    config = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        campaign="C2A_staged",
        hours=10,
        setpoints_patch=nested,
    )._session_config()
    cfg = config.setpoints["campaigns"]["C2A_staged"]
    assert cfg["max_hold_hr"] == 10
    target, ramp = CampaignManager(config.setpoints).get_temp_target(
        CampaignPhase.C2A_STAGED,
        4,
        MeltState(),
    )
    assert target == pytest.approx(1585.0)
    assert ramp == pytest.approx(175.0)


@pytest.mark.parametrize("mode", OXYGEN_SINK_CHANNEL_MODE_VALUES)
def test_oxygen_sink_channel_mode_round_trips_as_diagnostic_only(mode: str) -> None:
    patch = RecipePatch({OXYGEN_SINK_CHANNEL_MODE: mode})

    schema = RecipeSchema()
    nested = schema.to_setpoints_patch(patch)
    loaded = yaml.safe_load(yaml.safe_dump(nested, sort_keys=True))
    loaded_patch = RecipePatch.from_nested(loaded)
    assert loaded_patch.values[OXYGEN_SINK_CHANNEL_MODE] == mode

    run = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)
    config = run._session_config()
    assert config.setpoints["chemistry_kernel"][OXYGEN_SINK_CHANNEL_MODE_KEY] == mode

    session = SimSession().start(config)
    assert session.simulator.oxygen_sink_channel_mode.value == mode
    assert session.simulator._chem_kernel is not None
    assert session.simulator._chem_kernel.oxygen_sink_channel_mode.value == mode


def test_oxygen_sink_channel_mode_default_is_absent_from_setpoints_patch() -> None:
    config = PyrolysisRun(feedstock_id=FEEDSTOCK)._session_config()
    assert OXYGEN_SINK_CHANNEL_MODE_KEY not in config.setpoints.get(
        "chemistry_kernel", {}
    )

    session = SimSession().start(config)
    assert (
        session.simulator.oxygen_sink_channel_mode.value
        == "legacy_source_equilibrium"
    )


def test_oxygen_sink_channel_mode_rejects_unknown_value() -> None:
    with pytest.raises(RecipeValidationError, match="not in choices"):
        RecipePatch({OXYGEN_SINK_CHANNEL_MODE: "condensation_only_sink"}).validated()


def test_oxygen_sink_channel_mode_evalspec_round_trip_and_validation() -> None:
    mode = "deposit_gettering_diagnostic"
    spec = EvalSpec(
        recipe_id="recipe-id",
        feedstock_recipe_digest="feedstock-recipe-digest",
        feedstock_id=FEEDSTOCK,
        profile_id="profile-id",
        fidelity="fast",
        code_version="test-code-version",
        data_digests=DATA_DIGESTS,
        chemistry_kernel={OXYGEN_SINK_CHANNEL_MODE_KEY: mode},
    )
    payload = json.loads(canonical_evalspec_json(spec).decode("utf-8"))

    assert payload["chemistry_kernel"][OXYGEN_SINK_CHANNEL_MODE_KEY] == mode
    with pytest.raises(ValueError, match=OXYGEN_SINK_CHANNEL_MODE_KEY):
        EvalSpec(
            recipe_id="recipe-id",
            feedstock_recipe_digest="feedstock-recipe-digest",
            feedstock_id=FEEDSTOCK,
            profile_id="profile-id",
            fidelity="fast",
            code_version="test-code-version",
            data_digests=DATA_DIGESTS,
            chemistry_kernel={OXYGEN_SINK_CHANNEL_MODE_KEY: "behavior_mode"},
        )


def test_recipe_id_is_stable_and_schema_versioned() -> None:
    first = RecipePatch({PO2_DEFAULT: 9.0}).validated()
    second = RecipePatch.from_nested(
        {"campaigns": {"C0b_p_cleanup": {"pO2_mbar_default": 9.0}}}
    ).validated()

    assert first.recipe_id() == second.recipe_id()
    assert (
        first.recipe_id()
        == "2b42cde96b21ca9c9cb810d42da04359ffc7d8ca9983f2c32016b79d7bef78b9"
    )
    assert first.recipe_id(recipe_schema_version="recipe-schema-v2") != first.recipe_id()
    assert RecipePatch({PO2_DEFAULT: 8.0}).validated().recipe_id() != first.recipe_id()


def test_redox_cleanup_dose_fields_validate_but_do_not_materialize() -> None:
    schema = RecipeSchema()
    oxidant_spec = schema.spec_for(STAGE0_REDOX_OXIDANT_KG_PATH)
    carbon_spec = schema.spec_for(STAGE0_CARBON_REDUCTANT_KG_PATH)
    patch = RecipePatch(
        {
            STAGE0_REDOX_OXIDANT_KG_PATH: 12.5,
            STAGE0_CARBON_REDUCTANT_KG_PATH: 7.25,
        }
    ).validated(schema)

    assert oxidant_spec.search_enabled is False
    assert carbon_spec.search_enabled is False
    assert oxidant_spec.runtime_enabled is False
    assert carbon_spec.runtime_enabled is False
    assert STAGE0_REDOX_OXIDANT_KG_PATH not in {
        spec.path for spec in schema.search_allowlist
    }
    assert STAGE0_CARBON_REDUCTANT_KG_PATH not in {
        spec.path for spec in schema.search_allowlist
    }
    assert schema.to_setpoints_patch(patch) == {}
    assert schema.redox_cleanup_doses_kg(patch) == pytest.approx((12.5, 7.25))


def test_c5_allow_mre_voltage_cap_is_primary_search_knob() -> None:
    schema = RecipeSchema()
    cap_spec = schema.spec_for(C5_ALLOW_MRE_VOLTAGE_CAP_PATH)
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    owner_bound = _lookup_setpoint(
        setpoints,
        "campaigns.C5.allow_mre_voltage_cap_upper_bound_V",
    )
    branch_two = ("campaigns", "C5", "branch_two", "max_voltage_V")
    branch_one = ("campaigns", "C5", "branch_one", "max_voltage_V")
    search_paths = {spec.path for spec in schema.search_allowlist}

    assert cap_spec.search_enabled is True
    assert cap_spec.runtime_enabled is False
    assert cap_spec.low == pytest.approx(0.0)
    assert cap_spec.high == pytest.approx(owner_bound)
    assert C5_ALLOW_MRE_VOLTAGE_CAP_PATH in search_paths
    assert branch_two not in search_paths
    assert branch_one not in search_paths
    assert schema.spec_for(branch_two).runtime_enabled is True
    assert schema.spec_for(branch_one).runtime_enabled is True


def test_c4_hold_temp_is_optimizer_search_knob_not_setpoints_patch() -> None:
    schema = RecipeSchema()
    hold_spec = schema.spec_for(C4_HOLD_TEMP_C_PATH)
    search_paths = {spec.path for spec in schema.search_allowlist}

    assert C4_HOLD_TEMP_C_PATH in search_paths
    assert hold_spec.runtime_enabled is False
    assert hold_spec.low == pytest.approx(1580.0)
    assert hold_spec.high == pytest.approx(1670.0)
    assert schema.to_setpoints_patch(
        RecipePatch({C4_HOLD_TEMP_C_PATH: 1600.0})
    ) == {}


def test_c5_allow_mre_voltage_cap_rejects_above_owner_bound() -> None:
    schema = RecipeSchema()
    cap_spec = schema.spec_for(C5_ALLOW_MRE_VOLTAGE_CAP_PATH)
    assert cap_spec.high is not None
    too_high = float(cap_spec.high) + 0.01

    with pytest.raises(RecipeValidationError, match="above upper bound"):
        RecipePatch({C5_ALLOW_MRE_VOLTAGE_CAP_PATH: too_high}).validated(schema)


def test_forbidden_floor_cannot_be_neutered_by_custom_schema() -> None:
    # Review P1: a caller-supplied forbidden_prefixes ADDS to the inviolable class
    # floor; it can never remove it. RecipeSchema(forbidden_prefixes=()) must STILL
    # deny a *.products path, else the safety boundary is bypassable via a custom
    # schema passed to RecipePatch.validated().
    neutered = RecipeSchema(forbidden_prefixes=())
    with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
        RecipePatch({PRODUCT_TARGET: 1.0}).validated(neutered)

    # A caller addition is honored ON TOP OF the floor (extend, never replace).
    extended = RecipeSchema(forbidden_prefixes=("campaigns.C0",))
    assert extended.is_forbidden(("campaigns", "C0"))
    assert extended.is_forbidden(PRODUCT_TARGET)


def test_knob_bounds_source_provenance_is_honest() -> None:
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    range_sourced = 0
    engineering_envelopes = 0

    for spec in RecipeSchema().allowlist:
        if spec.bounds_source.startswith("setpoints:"):
            yaml_path = spec.bounds_source.removeprefix("setpoints:")
            yaml_value = _lookup_setpoint(setpoints, yaml_path)
            assert isinstance(yaml_value, list), (
                f"{'.'.join(spec.path)} cites scalar YAML as bounds_source; "
                "scalar nominal knobs must use engineering_envelope"
            )
            assert len(yaml_value) == 2
            assert spec.low is not None
            assert spec.high is not None
            assert yaml_value[0] <= spec.low <= spec.high <= yaml_value[1]
            range_sourced += 1
        else:
            assert spec.bounds_source.startswith("engineering_envelope"), (
                f"{'.'.join(spec.path)} bounds_source must be setpoints: range "
                "or engineering_envelope"
            )
            engineering_envelopes += 1

    assert range_sourced + engineering_envelopes == len(RecipeSchema().allowlist)
    assert engineering_envelopes > 0


def test_pressure_default_pair_map_covers_allowlisted_siblings() -> None:
    schema = RecipeSchema()
    allowlisted = {spec.path for spec in schema.allowlist}
    setpoints = yaml.safe_load(SETPOINTS_PATH.read_text())
    expected_pairs = {}

    for path in allowlisted:
        if len(path) != 3:
            continue
        if path[0] != "campaigns" or path[2] != "pO2_mbar_default":
            continue
        total_path = (path[0], path[1], "p_total_mbar_default")
        if total_path not in allowlisted:
            continue
        _lookup_setpoint(setpoints, ".".join(path))
        _lookup_setpoint(setpoints, ".".join(total_path))
        expected_pairs[path] = total_path

    assert dict(schema.PRESSURE_TOTAL_DEFAULT_BY_PO2_DEFAULT) == expected_pairs


def test_to_setpoints_patch_validates_before_rendering_forbidden_paths() -> None:
    patch = RecipePatch({("campaigns", "C2A", "products", "x"): 1.0})

    with pytest.raises(RecipeValidationError, match="forbidden recipe path"):
        RecipeSchema().to_setpoints_patch(patch)


def test_recipe_patch_refuses_explicit_partial_pressure_above_total() -> None:
    patch = RecipePatch({C3_PO2_DEFAULT: 1.2, C3_PTOTAL_DEFAULT: 0.8})

    with pytest.raises(RecipeValidationError, match="recipe_pressure_partial_exceeds_total"):
        patch.validated()


def test_to_setpoints_patch_keeps_po2_only_default_total_untouched() -> None:
    nested = RecipeSchema().to_setpoints_patch(RecipePatch({C3_PO2_DEFAULT: 0.8}))

    assert nested["campaigns"]["C3"]["pO2_mbar_default"] == pytest.approx(0.8)
    assert "p_total_mbar_default" not in nested["campaigns"]["C3"]
    config = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)._session_config()
    assert config.setpoints["campaigns"]["C3"]["p_total_mbar_default"] == pytest.approx(
        1.0
    )


def test_to_setpoints_patch_rejects_po2_only_above_default_total() -> None:
    with pytest.raises(RecipeValidationError, match="recipe_pressure_partial_exceeds_total"):
        RecipeSchema().to_setpoints_patch(RecipePatch({C3_PO2_DEFAULT: 1.2}))


def test_to_setpoints_patch_keeps_total_only_above_default_po2_untouched() -> None:
    nested = RecipeSchema().to_setpoints_patch(RecipePatch({C3_PTOTAL_DEFAULT: 1.2}))

    assert nested["campaigns"]["C3"]["p_total_mbar_default"] == pytest.approx(1.2)
    assert "pO2_mbar_default" not in nested["campaigns"]["C3"]
    config = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)._session_config()
    assert config.setpoints["campaigns"]["C3"]["pO2_mbar_default"] == pytest.approx(
        1.0
    )


def test_to_setpoints_patch_rejects_total_only_below_default_po2() -> None:
    with pytest.raises(RecipeValidationError) as exc_info:
        RecipeSchema().to_setpoints_patch(RecipePatch({C3_PTOTAL_DEFAULT: 0.6}))

    message = str(exc_info.value)
    assert "recipe_pressure_partial_exceeds_total" in message
    assert "campaigns.C3.pO2_mbar_default=1 (YAML default)" in message
    assert "campaigns.C3.p_total_mbar_default=0.6 (patched)" in message
    assert "set both pO2 and p_total knobs" in message


def test_po2_only_patch_recipe_id_differs_from_old_derived_total_effect() -> None:
    schema = RecipeSchema()
    po2_only = RecipePatch({C3_PO2_DEFAULT: 0.8}).validated(schema)
    explicit_old_derivation = RecipePatch(
        {C3_PO2_DEFAULT: 0.8, C3_PTOTAL_DEFAULT: 0.8}
    ).validated(schema)

    assert po2_only.recipe_id(schema) != explicit_old_derivation.recipe_id(schema)
    assert "p_total_mbar_default" not in po2_only.canonical_json()

    po2_only_config = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        setpoints_patch=schema.to_setpoints_patch(po2_only),
    )._session_config()
    explicit_config = PyrolysisRun(
        feedstock_id=FEEDSTOCK,
        setpoints_patch=schema.to_setpoints_patch(explicit_old_derivation),
    )._session_config()
    assert po2_only_config.setpoints["campaigns"]["C3"][
        "p_total_mbar_default"
    ] == pytest.approx(1.0)
    assert explicit_config.setpoints["campaigns"]["C3"][
        "p_total_mbar_default"
    ] == pytest.approx(0.8)


def test_dotted_path_segment_is_rejected() -> None:
    # Review P2: a segment embedding "." ("products.oxygen_kg" as ONE segment)
    # would slip past dotted-prefix "*.products" matching. Reject at normalization.
    with pytest.raises(RecipeValidationError, match="must not contain"):
        RecipePatch({("campaigns", "C0b_p_cleanup", "products.oxygen_kg"): 1.0})
