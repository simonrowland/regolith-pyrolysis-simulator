"""Measured condensation-route pins for physical output shifts."""

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.condensation import CondensationModel
from simulator.recipe_io import load_recipe_patch
from simulator.runner import PyrolysisRun
from simulator.state import CondensationTrain, EvaporationFlux, MeltState
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
    original = CondensationModel.route

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

    monkeypatch.setattr(CondensationModel, "route", measure)
    return totals


def test_route_totals_measure_only_returned_allocation(route_totals):
    model = CondensationModel(CondensationTrain(stages=[]))
    model.train.collection_route["stage3_route"] = "through"
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"Na": 0.5, "SiO": 0.5},
        stage_area_m2_by_stage={str(stage): 1.0 for stage in range(8)},
    )

    refused_flux = EvaporationFlux({"Na": 0.02, "SiO": 0.05})
    bypass_result = model.route(refused_flux, MeltState())

    assert model.last_stage3_route_diagnostic["event"] == "stage3_opening_refused"
    assert bypass_result.remaining_by_species["Na"] == pytest.approx(0.02)
    assert bypass_result.remaining_by_species["SiO"] == pytest.approx(0.05)
    assert route_totals["Na"]["inlet"] == pytest.approx(0.02)
    assert route_totals["Na"]["terminal"] == pytest.approx(0.02)
    assert route_totals["SiO"]["inlet"] == pytest.approx(0.05)
    assert route_totals["SiO"]["terminal"] == pytest.approx(0.05)

    model.train.collection_route["stage3_route"] = "through"
    passed_result = model.route(EvaporationFlux({"SiO": 0.03}), MeltState())

    assert passed_result.remaining_by_species["SiO"] == pytest.approx(0.03)
    # With no stages, all input remains terminal: Na 0.02 + 0 = 0.02 kg/h;
    # SiO 0.05 from the refused parcel + 0.03 from the admitted parcel = 0.08 kg/h.
    assert route_totals["Na"]["inlet"] == pytest.approx(0.02)
    assert route_totals["Na"]["terminal"] == pytest.approx(0.02)
    assert route_totals["SiO"]["inlet"] == pytest.approx(0.08)
    assert route_totals["SiO"]["terminal"] == pytest.approx(0.08)


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
        # Engine registration maps and engine_version strings are excluded
        # because host-specific registrations and builds are not run results.
        digest_payload, engine_version_count = _runner_digest_payload(runner_output)
        assert engine_version_count >= 1
        digest_serialized = json.dumps(digest_payload, sort_keys=True)
        actual["runner_sha256"] = hashlib.sha256(
            digest_serialized.encode()
        ).hexdigest()
        print(f"T1217_RUNNER_SHA256 {name} {actual['runner_sha256']}")
    # t-1604 rebaselined these kg from the production route after stage D_AB
    # moved to the film temperature T_film = (T_gas,in + T_wall) / 2.
    # lunar_24h and c2a_staged inlets are unchanged; captured kg fell and the
    # terminal rose by the same amount. full_lunar inlets, and its Fe wall
    # deposit, move only because that long run's evaporation follows the
    # changed capture. The wall-deposit diffusivity temperature is unchanged.
    expected = json.loads(PINS.read_text())[name]
    for species in SPECIES:
        if species not in expected:
            continue
        if name == "full_lunar" and species in {"Fe", "Cr", "SiO"}:
            assert actual[species] == expected[species]
        else:
            assert actual[species] == pytest.approx(
                expected[species], rel=1e-10, abs=1e-14
            )
    if runner_output is not None:
        assert actual["runner_sha256"] == expected["runner_sha256"]


def _runner_digest_payload(runner_output):
    # The active/registry maps and engine_version strings describe host-local
    # registrations/builds, not run results, so exclude both from this digest.
    # Requested registrations and the rest of the runner output remain hashed.
    payload = dict(runner_output)
    metadata = dict(payload["run_metadata"])
    engines_used = dict(metadata["engines_used"])
    del engines_used["active"]
    del engines_used["registry"]
    metadata["engines_used"] = engines_used
    payload["run_metadata"] = metadata
    return _without_engine_versions(payload)


def _without_engine_versions(value):
    if isinstance(value, dict):
        stripped = {}
        count = 0
        for key, item in value.items():
            if key == "engine_version":
                assert isinstance(item, str) and item.strip()
                count += 1
                continue
            stripped_item, nested_count = _without_engine_versions(item)
            stripped[key] = stripped_item
            count += nested_count
        return stripped, count
    if isinstance(value, list):
        stripped = []
        count = 0
        for item in value:
            stripped_item, nested_count = _without_engine_versions(item)
            stripped.append(stripped_item)
            count += nested_count
        return stripped, count
    return value, 0


@pytest.mark.nightly
@pytest.mark.xdist_group("magemin_fullrun_c")
@pytest.mark.timeout(5400)
def test_green_full_lunar_capture_pin(route_totals, tmp_path):
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
