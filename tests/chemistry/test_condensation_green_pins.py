"""Measured condensation-route pins for physical output shifts."""

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.condensation import CondensationModel
from simulator.recipe_io import load_recipe_patch
from simulator.runner import PyrolysisRun
from tests.chemistry.conftest import full_builtin_provider_run


SPECIES = ("Fe", "Na", "K", "Mg", "SiO")
PINS = Path(__file__).parents[1] / "fixtures" / "condensation_green.json"


@pytest.fixture
def route_totals(monkeypatch):
    totals = {
        species: {"inlet": 0.0, "terminal": 0.0, "wall": 0.0,
                  **{str(stage): 0.0 for stage in range(8)}}
        for species in SPECIES
    }
    original = CondensationModel._route

    def measure(model, flux, melt):
        result = original(model, flux, melt)
        for species in SPECIES:
            row = totals[species]
            row["inlet"] += flux.species_kg_hr.get(species, 0.0)
            row["terminal"] += result.remaining_by_species.get(species, 0.0)
            row["wall"] += result.wall_deposit_by_species.get(species, 0.0)
            for stage, captures in result.condensed_by_stage_species.items():
                row[str(stage)] += captures.get(species, 0.0)
        return result

    monkeypatch.setattr(CondensationModel, "_route", measure)
    return totals


def assert_pin(name, actual, tmp_path, runner_output=None):
    # Preserve measurements even when a deliberate physics change moves a pin.
    (tmp_path / f"{name}.json").write_text(json.dumps(actual, sort_keys=True))
    print(f"T1217_MEASUREMENT {name} {json.dumps(actual, sort_keys=True)}")
    if runner_output is not None:
        serialized = json.dumps(runner_output, sort_keys=True)
        (tmp_path / f"{name}-runner.json").write_text(serialized)
        actual["runner_sha256"] = hashlib.sha256(serialized.encode()).hexdigest()
        print(f"T1217_RUNNER_SHA256 {name} {actual['runner_sha256']}")
    expected = json.loads(PINS.read_text())[name]
    for species in SPECIES:
        assert actual[species] == pytest.approx(expected[species], rel=1e-10, abs=1e-14)
    if runner_output is not None:
        assert actual["runner_sha256"] == expected["runner_sha256"]


@pytest.mark.nightly
@pytest.mark.xdist_group("magemin_fullrun_c")
@pytest.mark.timeout(5400)
def test_green_full_lunar_capture_pin(route_totals, tmp_path):
    # Reuse the fixture's execution, with a fresh run AFTER the observer starts.
    # A cached package fixture would run before a function-scoped observer.
    _, _, sim = full_builtin_provider_run.__wrapped__(
        SimpleNamespace(param=("lunar_mare_low_ti", None))
    )
    assert sim.is_complete()
    assert_pin("full_lunar", route_totals, tmp_path)


@pytest.mark.parametrize("staged", [False, True], ids=["lunar_24h", "c2a_staged"])
def test_green_runner_capture_pin(staged, route_totals, tmp_path):
    patch = (load_recipe_patch(Path("data/recipes/c2a_staged_temperature_ladder.yaml"))
             if staged else {})
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A_staged" if staged else "C0",
        hours=9 if staged else 24,
        setpoints_patch=patch,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={"started_at_utc": "2026-05-15T00:00:00Z",
                                "kernel_commit_sha": "t1217-green"},
    )
    result = run.run()
    assert result["status"] == "ok"
    assert_pin("c2a_staged" if staged else "lunar_24h", route_totals, tmp_path, result)
