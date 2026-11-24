from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

import simulator.cost_parameters as cost_parameters_module
from simulator.lab_geometry import parse_lab_geometry
from simulator.recipe import RecipePatch, RecipeSchema, RecipeValidationError
from simulator.recipe_io import load_recipe_patch, write_recipe_patch


ROOT = Path(__file__).resolve().parents[1]
RECIPE_DIR = ROOT / "data" / "recipes"
EXAMPLE_RECIPE = RECIPE_DIR / "robinot_2026_benchtop_example.yaml"
ROBINOT_PRESET = ROOT / "data" / "presets" / "vacuum_pyrolysis" / "robinot_2026.yaml"
OPTIONAL_FIELDS = {"lab_schedule", "process_dt_s", "source_tags"}
GREEN_STARTER_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "recipe_schema"
    / "green_canonical_lunar_full_yield.yaml"
)
GREEN_WINNER_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "recipe_schema"
    / "green_prechange_winner.recipe.yaml"
)


def _robinot_preset() -> dict:
    return yaml.safe_load(ROBINOT_PRESET.read_text(encoding="utf-8"))


def test_legacy_recipe_database_starters_keep_their_setpoints_shape() -> None:
    assert RecipeSchema().recipe_schema_version == "recipe-schema-v2"
    for path in sorted(RECIPE_DIR.glob("*.yaml")):
        if path == EXAMPLE_RECIPE:
            continue
        original = yaml.safe_load(path.read_text(encoding="utf-8"))
        loaded = load_recipe_patch(path)

        assert not OPTIONAL_FIELDS.intersection(loaded)
        assert loaded == RecipeSchema().to_setpoints_patch(
            RecipePatch.from_nested(original)
        )


@pytest.mark.parametrize(
    "fixture_path",
    [GREEN_STARTER_FIXTURE, GREEN_WINNER_FIXTURE],
    ids=["starter", "prechange-winner"],
)
def test_green_recipe_fixtures_keep_load_save_parity(
    fixture_path: Path,
    tmp_path: Path,
) -> None:
    original_bytes = fixture_path.read_bytes()
    loaded = load_recipe_patch(fixture_path)
    original = yaml.safe_load(original_bytes)
    assert loaded == RecipeSchema().to_setpoints_patch(
        RecipePatch.from_nested(original)
    )
    saved_path = tmp_path / fixture_path.name
    write_recipe_patch(saved_path, loaded)
    assert load_recipe_patch(saved_path) == loaded


def test_green_starter_save_bytes_match_the_captured_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    virtual_checkout = Path("<checkout>")
    cost_path = virtual_checkout / "data" / "optimize_costs.yaml"
    cost_path.parent.mkdir(parents=True)
    cost_path.write_bytes((ROOT / "data" / "optimize_costs.yaml").read_bytes())
    monkeypatch.setattr(
        cost_parameters_module,
        "DEFAULT_COST_PARAMETERS_PATH",
        cost_path,
    )
    saved_path = (
        virtual_checkout / "data" / "recipes" / "starter.recipe.yaml"
    )

    write_recipe_patch(saved_path, load_recipe_patch(GREEN_STARTER_FIXTURE))

    assert saved_path.read_bytes() == GREEN_STARTER_FIXTURE.read_bytes()


def _assert_setpoint_document_contains(
    actual: dict,
    expected: dict,
    path: tuple[str, ...] = (),
) -> None:
    for key, expected_value in expected.items():
        current_path = (*path, key)
        if isinstance(expected_value, dict):
            assert isinstance(actual.get(key), dict), ".".join(current_path)
            _assert_setpoint_document_contains(
                actual[key],
                expected_value,
                current_path,
            )
        else:
            assert actual.get(key) == expected_value, ".".join(current_path)


def test_green_starter_fixture_keeps_runner_hours_feedstock_and_setpoints() -> None:
    # Load the backend module first to avoid the thermal_budget import cycle
    # exercised by the runner's public entry.
    import engines.builtin.vapor_pressure
    from simulator.runner import PyrolysisRun

    fixture = yaml.safe_load(GREEN_STARTER_FIXTURE.read_bytes())
    loaded = load_recipe_patch(GREEN_STARTER_FIXTURE)
    expected_setpoints = RecipeSchema().to_setpoints_patch(
        RecipePatch.from_nested(fixture)
    )
    hours = fixture["campaigns"]["C2A_continuous"]["max_hold_hr"]
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        hours=hours,
        setpoints_patch=loaded,
    )

    config = run._session_config()

    assert config.hours == hours
    assert config.feedstock_id == "lunar_mare_low_ti"
    _assert_setpoint_document_contains(config.setpoints, expected_setpoints)


def test_lab_schedule_and_source_tags_round_trip_without_losing_knots(
    tmp_path: Path,
) -> None:
    preset = _robinot_preset()
    schedule = copy.deepcopy(preset["lab_schedule"])
    source_tags = {
        "feedstock.total_Fe": {
            "source_class": "literature_sidecar",
            "citation_id": "engelschion_2020",
            "locator": "EAC-1A XRF major-oxide table; total Fe reported as FeO",
        },
        "run.assumed_FeO": {
            "source_class": "assumption_with_sensitivity_marker",
            "registry_default_id": "test_default_feo_basis",
            "flag": {"kind": "assumed_input", "reason": "redox split not measured"},
        },
    }
    recipe_path = tmp_path / "robinot_example.recipe.yaml"
    write_recipe_patch(
        recipe_path,
        {
            "lab_schedule": schedule,
            "process_dt_s": preset["process_dt_s"],
            "source_tags": source_tags,
        },
    )
    loaded = load_recipe_patch(recipe_path)

    original_knots = yaml.safe_dump(
        schedule["melt_temperature_C"],
        sort_keys=False,
    ).encode("utf-8")
    loaded_knots = yaml.safe_dump(
        loaded["lab_schedule"]["melt_temperature_C"],
        sort_keys=False,
    ).encode("utf-8")
    assert loaded_knots == original_knots
    assert loaded["process_dt_s"] == 60.0
    assert loaded["source_tags"] == source_tags
    assert loaded["source_tags"]["run.assumed_FeO"] != loaded["source_tags"][
        "feedstock.total_Fe"
    ]


@pytest.mark.parametrize("dt_s", [0, -1, float("inf"), float("nan"), True])
def test_process_dt_s_requires_a_positive_finite_number(dt_s: object) -> None:
    with pytest.raises(RecipeValidationError):
        RecipePatch.from_nested({"process_dt_s": dt_s})


def test_process_dt_s_uses_the_landed_timestep_owner_for_duration_ratio() -> None:
    schedule = _robinot_preset()["lab_schedule"]
    minute_step = RecipePatch.from_nested(
        {"lab_schedule": schedule, "process_dt_s": 60}
    )
    assert minute_step.process_dt_s == 60.0

    with pytest.raises(RecipeValidationError, match="timestep_does_not_divide_duration"):
        RecipePatch.from_nested(
            {"lab_schedule": schedule, "process_dt_s": 700}
        )


def test_robinot_geometry_keeps_gram_mass_units() -> None:
    preset = _robinot_preset()
    geometry = parse_lab_geometry(
        preset["lab_geometry"],
        allow_temperature_profiles=True,
    )
    assert geometry is not None
    assert geometry.sample_mass_g == pytest.approx(3.38)


def test_legacy_ramp_recipe_has_no_lab_schedule_override() -> None:
    recipe = load_recipe_patch(RECIPE_DIR / "canonical_lunar_full_yield.yaml")
    assert "lab_schedule" not in recipe
    assert "process_dt_s" not in recipe


def test_robinot_example_recipe_uses_preset_geometry_and_distinct_fe_tags() -> None:
    recipe = load_recipe_patch(EXAMPLE_RECIPE)
    preset = _robinot_preset()
    geometry = parse_lab_geometry(
        preset["lab_geometry"],
        allow_temperature_profiles=True,
    )

    assert geometry is not None
    assert geometry.sample_mass_g == pytest.approx(3.38)
    assert recipe["process_dt_s"] == 60.0
    assert recipe["lab_schedule"]["id"] == preset["lab_schedule"]["id"]
    assert recipe["source_tags"]["feedstock.total_Fe"]["source_class"] == (
        "literature_sidecar"
    )
    assert recipe["source_tags"]["run.assumed_FeO"]["source_class"] == (
        "assumption_with_sensitivity_marker"
    )
