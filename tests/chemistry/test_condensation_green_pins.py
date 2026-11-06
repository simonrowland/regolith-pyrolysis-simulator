"""Measured condensation-route pins for physical output shifts."""

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.condensation import CondensationModel
from simulator.condensation_routing import stage_key_for_stage
from simulator.recipe_io import load_recipe_patch
from simulator.runner import PyrolysisRun
from tests.chemistry.conftest import full_builtin_provider_run


SPECIES = ("Fe", "Na", "K", "Mg", "SiO", "Cr")
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
            for stage_key, captures in result.condensed_by_stage_species.items():
                stage_number = next(
                    stage.stage_number
                    for stage in model.train.stages
                    if stage_key_for_stage(
                        stage,
                        falsey_sub_order_is_zero=True,
                        include_collector_fallback=True,
                    ) == stage_key
                )
                row[str(stage_number)] += captures.get(species, 0.0)
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
        run_metadata = runner_output["run_metadata"]
        assert isinstance(run_metadata, dict)
        engines_used = run_metadata["engines_used"]
        assert isinstance(engines_used, dict)
        assert {"active", "requested", "registry"} <= engines_used.keys()
        active = engines_used["active"]
        registry = engines_used["registry"]
        assert isinstance(active, dict)
        assert isinstance(registry, dict)
        authoritative = {}
        for intent, slots in registry.items():
            assert isinstance(intent, str) and intent
            assert isinstance(slots, dict)
            assert {"authoritative", "fallback", "shadows"} <= slots.keys()
            assert isinstance(slots["shadows"], list)
            for provider in (
                slots["authoritative"],
                slots["fallback"],
                *slots["shadows"],
            ):
                if provider is not None:
                    assert isinstance(provider, str) and provider
                    assert provider == provider.strip()
                    assert provider[0].isalnum()
                    assert all(
                        char.isalnum() or char in "._:-" for char in provider
                    )
            if slots["authoritative"] is not None:
                authoritative[intent] = slots["authoritative"]
        assert active == authoritative
        assert active
        # Assert the emitted provider maps above, but keep their host-specific
        # registrations out of this digest; every other result field remains.
        digest_payload = _runner_digest_payload(runner_output)
        digest_serialized = json.dumps(digest_payload, sort_keys=True)
        actual["runner_sha256"] = hashlib.sha256(
            digest_serialized.encode()
        ).hexdigest()
        print(f"T1217_RUNNER_SHA256 {name} {actual['runner_sha256']}")
    expected = json.loads(PINS.read_text())[name]
    # Collect every species before failing: stopping at the first mismatch
    # hid stale Cr/SiO pins behind an Fe miss through two repins.
    mismatches = {}
    for species in SPECIES:
        if species not in expected:
            continue
        if name == "full_lunar" and species in {"Fe", "Cr", "SiO"}:
            matches = actual[species] == expected[species]
        else:
            matches = actual[species] == pytest.approx(
                expected[species], rel=1e-10, abs=1e-14
            )
        if not matches:
            mismatches[species] = {
                "actual": actual[species],
                "expected": expected[species],
            }
    assert not mismatches, mismatches
    if runner_output is not None:
        assert actual["runner_sha256"] == expected["runner_sha256"]


def _runner_digest_payload(runner_output):
    # active and registry are projections of capability registrations, which
    # vary with each host's engine configuration; requested and all other
    # metadata remain in the hash so unrelated result changes still fail it.
    payload = dict(runner_output)
    metadata = dict(payload["run_metadata"])
    engines_used = dict(metadata["engines_used"])
    del engines_used["active"]
    del engines_used["registry"]
    metadata["engines_used"] = engines_used
    payload["run_metadata"] = metadata
    # engine_version strings carry the local engine binary's build digest
    # (e.g. MAGEMin 1.9.6 built separately on the laptop and on studio-3), so
    # they are host provenance, not results. A laptop-vs-studio-3 field diff of
    # the full runner result found them to be the only remaining difference.
    return _without_engine_versions(payload)


def _without_engine_versions(value):
    if isinstance(value, dict):
        stripped = {}
        for key, item in value.items():
            if key == "engine_version":
                assert isinstance(item, str) and item.strip()
                continue
            stripped[key] = _without_engine_versions(item)
        return stripped
    if isinstance(value, list):
        return [_without_engine_versions(item) for item in value]
    return value


@pytest.mark.nightly
@pytest.mark.xdist_group("magemin_fullrun_c")
@pytest.mark.timeout(5400)
def test_green_full_lunar_capture_pin(route_totals, tmp_path):
    # full_lunar Fe capture totals are the studio-3 actuals (pins2 log, tree
    # 9666fc129 plus the de32c69ca runner regen); Cr and SiO are the studio-3
    # actuals at 16fcc5985, identical across two isolated runs. Lunar full runs
    # diverge between the laptop and studio-3, so this pin is not a laptop re-run.
    # The production band pin records the controller's intended area-weighted
    # cutoff: 33 equal strips span 900-1200 C, and only 17 strips at/below the
    # inclusive 1050 C SiO reactive-product cutoff capture. The other 16 pass
    # through. Round-3 midpoint totals were Stage 3/4/terminal 6.937950284 /
    # 6.226604401 / 308.156143023 kg; round-4 band totals are 3.484007094 /
    # 6.322898104 / 311.513785826 kg. No Stage-3 SiO refusals occurred.
    # Reuse the fixture's execution, with a fresh run AFTER the observer starts.
    # A cached package fixture would run before a function-scoped observer.
    _, _, sim = full_builtin_provider_run.__wrapped__(
        SimpleNamespace(param=("lunar_mare_low_ti", None))
    )
    assert sim.is_complete()
    assert_pin("full_lunar", route_totals, tmp_path)


@pytest.mark.parametrize("staged", [False, True], ids=["lunar_24h", "c2a_staged"])
def test_green_runner_capture_pin(staged, route_totals, tmp_path):
    # Studio-3 with openimcc 9f25dfe: t-1637's liquid-standard notices
    # (fafbee7c4 + d-346) change serialized notices; capture totals are
    # numeric-unchanged.
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
