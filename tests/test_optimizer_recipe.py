from __future__ import annotations

import yaml
import pytest

from simulator.optimize.recipe import (
    KnobSpec,
    RecipePatch,
    RecipeSchema,
    RecipeValidationError,
)
from simulator.runner import PyrolysisRun


FEEDSTOCK = "lunar_mare_low_ti"
PO2_DEFAULT = ("campaigns", "C0b_p_cleanup", "pO2_mbar_default")
PRODUCT_TARGET = ("campaigns", "C0b_p_cleanup", "products", "oxygen_kg")


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
            ("campaigns", "C2A_continuous", "duration_h"): [20, 24],
        }
    ).validated()

    nested = patch.to_nested()
    loaded = yaml.safe_load(yaml.safe_dump(nested, sort_keys=True))
    assert RecipePatch.from_nested(loaded).values == patch.values

    run = PyrolysisRun(feedstock_id=FEEDSTOCK, setpoints_patch=nested)
    config = run._session_config()
    assert config.setpoints["campaigns"]["C0b_p_cleanup"]["pO2_mbar_default"] == 10.0
    assert config.setpoints["campaigns"]["C2A_continuous"]["duration_h"] == [
        20,
        24,
    ]


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
    assert RecipePatch({PO2_DEFAULT: 10.0}).validated().recipe_id() != first.recipe_id()


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


def test_dotted_path_segment_is_rejected() -> None:
    # Review P2: a segment embedding "." ("products.oxygen_kg" as ONE segment)
    # would slip past dotted-prefix "*.products" matching. Reject at normalization.
    with pytest.raises(RecipeValidationError, match="must not contain"):
        RecipePatch({("campaigns", "C0b_p_cleanup", "products.oxygen_kg"): 1.0})
