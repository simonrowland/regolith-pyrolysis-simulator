from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.render_optimizer_hero import render_hero_tables
from simulator.optimize.profiles import load_profile


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "optimizer"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_hero_tables_render_study_and_owner_run_output() -> None:
    output = render_hero_tables(
        _fixture("hero-study-summary.json"),
        _fixture("hero-winner-run.json"),
        commit="abc123",
    )

    assert "| Fe | 0.550 | 0.450 | 0.000 |" in output
    assert "| Si | 0.550 | 0.400 | 0.050 |" in output
    assert "| Rump mass | 0.348 kg |" in output
    assert "Al2O3 57.5 wt%; CaO 42.5 wt%" in output
    assert "| O₂ released from melt | 0.200 kg |" in output
    assert "| Backend | internal-analytical |" in output
    assert "| Seed | 17 |" in output
    assert "| Budget | 4 |" in output
    assert "| Commit | abc123 |" in output


def test_hero_table_tracks_melt_boundary_owner_mutation() -> None:
    run_report = _fixture("hero-winner-run.json")
    run_report = copy.deepcopy(run_report)
    run_report["yield_disposition"]["fraction_table"]["rows"][0][
        "destination_fractions"
    ]["product_tapped"] = 0.1
    run_report["yield_disposition"]["fraction_table"]["rows"][0][
        "destination_fractions"
    ]["melt_retained"] = 0.55

    output = render_hero_tables(
        _fixture("hero-study-summary.json"),
        run_report,
        commit="abc123",
    )

    assert "| Fe | 0.250 | 0.750 | 0.000 |" in output


def test_hero_table_refuses_a_study_without_a_feasible_winner() -> None:
    summary = _fixture("hero-study-summary.json")
    summary["winner"] = None

    with pytest.raises(ValueError, match="no feasible winner"):
        render_hero_tables(
            summary,
            _fixture("hero-winner-run.json"),
            commit="abc123",
        )


def test_hero_profile_uses_only_existing_schema_fields() -> None:
    profile = load_profile(
        ROOT / "data" / "optimize_profiles" / "hero" / "lunar_mare_low_ti.yaml"
    )

    assert profile["feedstock"] == "lunar_mare_low_ti"
    assert profile["run"] == {
        "campaign": "C2A",
        "hours": 225,
        "mass_kg": 1.0,
        "backend_name": "internal-analytical",
    }
    assert profile["pinned_paths"] == ["furnace_max_T_C"]
    assert len(profile["seed_recipes"]) == 1
    assert profile["seed_recipes"][0]["id"] == "canonical-lunar-full-yield"
    assert profile["seed_recipes"][0]["patch"]["furnace_max_T_C"] == 2200
