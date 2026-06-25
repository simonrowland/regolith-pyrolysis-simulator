from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.recipe_io import (
    RecipeIOError,
    load_recipe_patch,
    read_recipe_metadata,
)
from simulator.runner import PyrolysisRun


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RECIPE_DIR = DATA_DIR / "recipes"


def test_named_recipe_library_example_loads_as_setpoints_patch() -> None:
    patch = load_recipe_patch(RECIPE_DIR / "c2a_staged_temperature_ladder.yaml")

    config = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A_staged",
        hours=1,
        setpoints_patch=patch,
        allow_fallback_vapor=True,
    )._session_config()

    c2a = config.setpoints["campaigns"]["C2A_staged"]
    assert c2a["max_hold_hr"] == 9
    assert c2a["stages"][0]["name"] == "alkali_early_fe"
    assert c2a["stages"][0]["target_C"] == 1250
    assert c2a["stages"][1]["target_C"] == 1600
    assert c2a["stages"][3]["target_C"] == 1150


def test_runner_recipe_cli_honors_setpoints_patch(tmp_path: Path) -> None:
    schema = RecipeSchema()
    recipe = schema.to_setpoints_patch(
        RecipePatch({
            ("campaigns", "C3", "alkali_dosing", "Na_kg"): 12.0,
        })
    )
    recipe_path = tmp_path / "na_dose.recipe.yaml"
    recipe_path.write_text(yaml.safe_dump(recipe, sort_keys=True), encoding="utf-8")
    output_path = tmp_path / "run.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock",
            "lunar_mare_low_ti",
            "--campaign",
            "C3_NA",
            "--hours",
            "1",
            "--recipe",
            str(recipe_path),
            "--allow-fallback-vapor",
            "--started-at-utc",
            "2026-05-15T00:00:00Z",
            "--kernel-commit-sha",
            "recipe-test",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["run_metadata"]["additives_kg"] == {"Na": 12.0}


def test_malformed_recipe_fails_loud(tmp_path: Path) -> None:
    recipe_path = tmp_path / "bad.recipe.yaml"
    recipe_path.write_text(
        "mass_balance:\n  tolerance_pct: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(RecipeIOError, match="unknown top-level recipe key"):
        load_recipe_patch(recipe_path)


def test_metadata_recipe_loads_patch_unchanged(tmp_path: Path) -> None:
    base_patch = load_recipe_patch(RECIPE_DIR / "c2a_staged_temperature_ladder.yaml")
    recipe_path = tmp_path / "metadata.recipe.yaml"
    metadata = {
        "title": "C2A staged capture",
        "created_utc": "2026-06-23T00:00:00Z",
        "feedstock": "lunar_mare_low_ti",
        "campaign": "C2A_staged",
        "headline_recipe": {
            "feedstock": "lunar_mare_low_ti",
            "campaign": "C2A_staged",
            "temperature_ladder": [],
        },
        "headline_results": {
            "oxygen_kg": 12.5,
            "energy_kWh": 42.0,
            "wall_deposit_kg": 0.0,
        },
    }
    recipe_path.write_text(
        yaml.safe_dump({"metadata": metadata, **base_patch}, sort_keys=False),
        encoding="utf-8",
    )

    assert read_recipe_metadata(recipe_path) == metadata
    assert load_recipe_patch(recipe_path) == base_patch


def test_malformed_metadata_fails_loud(tmp_path: Path) -> None:
    recipe_path = tmp_path / "bad-metadata.recipe.yaml"
    recipe_path.write_text(
        "metadata:\n"
        "  title: 7\n"
        "  headline_recipe: {}\n"
        "  headline_results: {}\n"
        "furnace_max_T_C: 1800\n",
        encoding="utf-8",
    )

    with pytest.raises(RecipeIOError, match="metadata.title"):
        load_recipe_patch(recipe_path)


def test_stage_recipe_must_be_optimizer_shape(tmp_path: Path) -> None:
    recipe_path = tmp_path / "partial-stage.recipe.yaml"
    recipe_path.write_text(
        "campaigns:\n"
        "  C2A_staged:\n"
        "    stages:\n"
        "    - name: alkali_early_fe\n"
        "      target_C: 1250\n",
        encoding="utf-8",
    )

    with pytest.raises(RecipeIOError, match="optimizer setpoints_patch shape"):
        load_recipe_patch(recipe_path)


def test_save_recipe_helper_normalizes_optimizer_winner(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "optimizer"
    source_dir.mkdir()
    source = source_dir / "winner.recipe.yaml"
    source.write_text(
        (RECIPE_DIR / "c2a_staged_temperature_ladder.yaml").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    destination = tmp_path / "recipes" / "saved_c2a.yaml"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "save_recipe.py"),
            "--library-dir",
            str(tmp_path / "recipes"),
            str(source_dir),
            "saved_c2a",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(destination)
    assert load_recipe_patch(destination) == load_recipe_patch(source)


def test_save_recipe_helper_round_trips_saved_recipe_through_runner(
    tmp_path: Path,
) -> None:
    schema = RecipeSchema()
    recipe = schema.to_setpoints_patch(
        RecipePatch({
            ("campaigns", "C3", "alkali_dosing", "Na_kg"): 7.0,
        })
    )
    source_dir = tmp_path / "optimizer"
    source_dir.mkdir()
    source = source_dir / "winner.recipe.yaml"
    source.write_text(yaml.safe_dump(recipe, sort_keys=True), encoding="utf-8")
    library_dir = tmp_path / "recipes"

    saved = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "save_recipe.py"),
            "--library-dir",
            str(library_dir),
            str(source_dir),
            "saved_na",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert saved.returncode == 0, saved.stderr
    saved_recipe = Path(saved.stdout.strip())
    output_path = tmp_path / "run.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock",
            "lunar_mare_low_ti",
            "--campaign",
            "C3_NA",
            "--hours",
            "1",
            "--recipe",
            str(saved_recipe),
            "--allow-fallback-vapor",
            "--started-at-utc",
            "2026-05-15T00:00:00Z",
            "--kernel-commit-sha",
            "recipe-test",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["run_metadata"]["additives_kg"] == {"Na": 7.0}


def test_runner_recipe_runtime_campaign_overrides_win_same_key(
    tmp_path: Path,
) -> None:
    schema = RecipeSchema()
    recipe = schema.to_setpoints_patch(
        RecipePatch({
            ("campaigns", "C4", "pO2_mbar"): 0.1,
        })
    )
    recipe_path = tmp_path / "c4-po2.recipe.yaml"
    recipe_path.write_text(yaml.safe_dump(recipe, sort_keys=True), encoding="utf-8")
    output_path = tmp_path / "run.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock",
            "lunar_mare_low_ti",
            "--campaign",
            "C4",
            "--hours",
            "1",
            "--recipe",
            str(recipe_path),
            "--runtime-campaign-overrides",
            json.dumps({"C4": {"pO2_mbar": 0.3}}),
            "--allow-fallback-vapor",
            "--started-at-utc",
            "2026-05-15T00:00:00Z",
            "--kernel-commit-sha",
            "recipe-test",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "ok"
    assert payload["per_hour_summary"][0]["pO2_bar"] == pytest.approx(0.0003)


def test_runner_malformed_recipe_cli_fails_loud(tmp_path: Path) -> None:
    recipe_path = tmp_path / "bad.recipe.yaml"
    recipe_path.write_text(
        "mass_balance:\n  tolerance_pct: 1\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "run.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            "--feedstock",
            "lunar_mare_low_ti",
            "--campaign",
            "C0",
            "--hours",
            "1",
            "--recipe",
            str(recipe_path),
            "--allow-fallback-vapor",
            "--started-at-utc",
            "2026-05-15T00:00:00Z",
            "--kernel-commit-sha",
            "recipe-test",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert "unknown top-level recipe key" in payload["error_message"]


def test_no_recipe_run_matches_committed_golden_text() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=24,
        additives_kg={},
        allow_fallback_vapor=True,
        run_metadata_overrides={
            "started_at_utc": "2026-05-15T00:00:00Z",
            "kernel_commit_sha": "goal-18-fixture",
        },
    )

    actual = json.dumps(run.run(), indent=2, sort_keys=False)
    expected = (
        ROOT / "tests" / "fixtures" / "runner" / "lunar_mare_low_ti_C0_24h.json"
    ).read_text(encoding="utf-8")
    assert actual == expected
