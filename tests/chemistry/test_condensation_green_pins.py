"""Measured condensation-route pins for physical output shifts."""

import json
import hashlib
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.reduced_real_determinism import (
    ENVIRONMENT_RETRIES_KEY,
    failed_provider_attempt_is_environment,
    gate_curve_provider_diagnostic_reasons,
    gate_curve_provider_timeout_reason,
)

from simulator.condensation import CondensationModel
from simulator.condensation_routing import stage_key_for_stage
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

    def measure(model, flux, melt, **kwargs):
        result = original(model, flux, melt, **kwargs)
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
    if runner_output is not None:
        assert runner_output.get("status") == "ok", (
            "runner output must complete successfully before pinning its "
            f"digest; status={runner_output.get('status')!r}, "
            f"reason={runner_output.get('reason')!r}, "
            f"error_message={runner_output.get('error_message')!r}"
        )
        assert runner_output.get("per_hour_summary"), (
            "runner output has no completed hours to include in its digest"
        )
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
        # engine_version strings rode only on the alphaMELTS continuation
        # records. Since b-1303 that continuation runs only when MAGEMin has
        # no usable curve, so a clean run can strip zero of them. Engine
        # identity is still asserted above through engines_used["active"].
        digest_payload, _engine_version_count = _runner_digest_payload(
            runner_output
        )
        digest_serialized = json.dumps(digest_payload, sort_keys=True)
        actual["runner_sha256"] = hashlib.sha256(
            digest_serialized.encode()
        ).hexdigest()
        print(f"T1217_RUNNER_SHA256 {name} {actual['runner_sha256']}")
    # lunar_24h and c2a_staged are the executable route on this tree.
    # The previous numbers predate the Stage-1 plate already in
    # 0a361d54c (0.2828 m2, wall 1100 C). On lunar C0, Mars C2A, and CI,
    # Stage-1 Fe is identical to that parent: stage 0 has no capture
    # area, so the Stage-1 film is still the train gas temperature.
    # t-1626 cools the gas for the later stages, their capture falls,
    # and the terminal rises by that mass. t-1604 remains D_AB at
    # T_film = (T_gas,in + T_wall) / 2, with T_gas,in now this stage's
    # bulk inlet. b1113 remains the removal of the designated-species
    # skip. full_lunar was re-run on studio-2 at a2299343eb (b271 regen
    # tip) and matches the d-441 owner card for b265: under d-402 each
    # stage captures what its wall saturation pressure condenses, so
    # Stage 4 now holds Fe 74.20, SiO 255.54 and Mg 30.11 kg, and the
    # terminal falls (Fe 85.64 -> 18.04, SiO 311.51 -> 59.99 kg).
    # 2026-11-26 b274 receipt 6bac433a at b7a91af39c. Every species moved.
    # Ledger fO2 (a95942989e) and the d-469 floor (8f174cc807) put the
    # melt on the Fe line, so Fe, Cr, Na, K and Mg capture change. The
    # oxide-Fe debit (f187184015) leaves oxygen in the melt; that oxygen
    # books the SiO oxidative wall leg, which was 0 under the old pin.
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
    return _without_engine_versions(deterministic_gate_payload(payload))


def _without_environment_attempts(value):
    """Drop load-timeout failed-provider attempts from a deterministic compare.

    Returns the same object when nothing is removed, so ``sort_keys=False``
    golden text stays byte-identical on a clean run. The production payload
    still carries the attempt.
    """

    if isinstance(value, dict):
        rebuilt = None
        for key, item in value.items():
            if key == ENVIRONMENT_RETRIES_KEY:
                if rebuilt is None:
                    rebuilt = dict(value)
                rebuilt.pop(key, None)
                continue
            if key == "failed_providers" and isinstance(item, (list, tuple)):
                kept = []
                changed = False
                for attempt in item:
                    if (
                        isinstance(attempt, Mapping)
                        and failed_provider_attempt_is_environment(attempt)
                    ):
                        changed = True
                        continue
                    stripped_attempt = _without_environment_attempts(attempt)
                    if stripped_attempt is not attempt:
                        changed = True
                    kept.append(stripped_attempt)
                if changed:
                    if rebuilt is None:
                        rebuilt = dict(value)
                    rebuilt[key] = kept if isinstance(item, list) else type(item)(kept)
                continue
            stripped_item = _without_environment_attempts(item)
            if stripped_item is not item:
                if rebuilt is None:
                    rebuilt = dict(value)
                rebuilt[key] = stripped_item
        return value if rebuilt is None else rebuilt
    if isinstance(value, list):
        rebuilt = None
        for index, item in enumerate(value):
            stripped_item = _without_environment_attempts(item)
            if stripped_item is not item:
                if rebuilt is None:
                    rebuilt = list(value)
                rebuilt[index] = stripped_item
        return value if rebuilt is None else rebuilt
    if isinstance(value, tuple):
        rebuilt = None
        for index, item in enumerate(value):
            stripped_item = _without_environment_attempts(item)
            if stripped_item is not item:
                if rebuilt is None:
                    rebuilt = list(value)
                rebuilt[index] = stripped_item
        return value if rebuilt is None else tuple(rebuilt)
    return value


_MISSING = object()

_ENVIRONMENT_HOLD_MESSAGE = (
    "environment: gate_provider_refusal records a load timeout "
    "(disposition=environment); the held-last-good curve is not a golden mismatch"
)


def _refusal_is_environment_hold(refusal: Mapping) -> bool:
    return (
        refusal.get("disposition") == "environment"
        or refusal.get("backend_failure_disposition") == "environment"
    )


def _refuse_environment_gate_hold(value) -> None:
    """A held curve is an environment failure, not a golden mismatch.

    A recovered timeout has no ``gate_provider_refusal``. A stable
    out-of-domain refusal has no environment disposition. Both compare.
    """

    seen: set[int] = set()

    def walk(node) -> None:
        if node is None or isinstance(node, (str, bytes, int, float, bool)):
            return
        marker = id(node)
        if marker in seen:
            return
        seen.add(marker)
        if isinstance(node, Mapping):
            refusal = node.get("gate_provider_refusal")
            if isinstance(refusal, Mapping) and _refusal_is_environment_hold(refusal):
                raise AssertionError(_ENVIRONMENT_HOLD_MESSAGE)
            for item in node.values():
                walk(item)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        for name in ("record", "snapshots", "liquid_fraction_gate"):
            child = getattr(node, name, _MISSING)
            if child is not _MISSING:
                walk(child)

    walk(value)


def deterministic_gate_payload(value):
    """Gate payload a golden or digest compares.

    Load-timeout attempts stay in the production payload and are omitted here.
    A held-last-good curve refuses before that compare.
    """

    _refuse_environment_gate_hold(value)
    return _without_environment_attempts(value)


def _planner_attempt(diagnostic, provider="magemin-shadow"):
    """The attempt record ``planner._dispatch_gate_provider_order`` stores."""

    reasons = gate_curve_provider_diagnostic_reasons(diagnostic)
    return {
        "provider": provider,
        "status": "not_converged",
        "reason": "; ".join(reasons) or "not_converged",
        "diagnostic": diagnostic,
    }


def _timeout_escape_diagnostics():
    """The four shapes that retry and used to miss the digest predicate."""

    from simulator.engine_pool import EngineWorkerTimeout
    from simulator.melt_backend.liquidus import liquidus_sample_error_from_exception

    warm = liquidus_sample_error_from_exception(
        EngineWorkerTimeout("thermoengine", 15.0, phase="equilibrium"),
    )
    return {
        "alphamelts_gate_cooling_path_timeout": {"reason": "timeout"},
        "liquidus_aggregate_budget_exceeded": {
            "reason": "aggregate_budget_exceeded",
        },
        "magemin_aggregate_budget_exceeded": {
            "backend_status": "not_converged",
            "backend_failure_exception": "MAGEMinAggregateBudgetExceeded",
        },
        "magemin_subprocess_slot_timeout": {
            "backend_status": "not_converged",
            "backend_failure_exception": "MAGEMinSubprocessSlotTimeout",
        },
        "thermoengine_warm_call_equilibrium_timeout": dict(warm.diagnostics),
    }


def _timeout_escape_attempts():
    return [
        _planner_attempt(diagnostic)
        for diagnostic in _timeout_escape_diagnostics().values()
    ]


def test_timeout_escape_shapes_match_the_retry_predicate():
    for name, diagnostic in _timeout_escape_diagnostics().items():
        attempt = _planner_attempt(diagnostic)
        assert gate_curve_provider_timeout_reason(diagnostic) is not None, name
        assert failed_provider_attempt_is_environment(attempt) is (
            gate_curve_provider_timeout_reason(attempt) is not None
            or gate_curve_provider_timeout_reason(diagnostic) is not None
        ), name
        assert failed_provider_attempt_is_environment(attempt) is True, name


@pytest.mark.parametrize(
    "reason",
    [
        "composition_projected",
        "incomplete_cooling_path",
        "no_convergence",
        "not_converged",
    ],
)
def test_physics_gate_reason_is_not_an_environment_attempt(reason):
    diagnostic = {
        "reason": reason,
        "backend_status_reason": reason,
        "backend_failure_disposition": "environment",
    }
    attempt = _planner_attempt(diagnostic)
    attempt["disposition"] = "environment"
    assert gate_curve_provider_timeout_reason(diagnostic) is None
    assert failed_provider_attempt_is_environment(attempt) is False


def test_deterministic_gate_payload_drops_only_environment_attempts():
    clean = {
        "gate_provider_step_down": {
            "failed_providers": [
                {"provider": "magemin-shadow", "reason": "composition_projected"},
                {
                    "provider": "alphamelts-diagnostic",
                    "reason": "incomplete_cooling_path",
                },
            ],
        },
    }
    assert deterministic_gate_payload(clean) is clean
    assert json.dumps(clean, sort_keys=False) == json.dumps(
        deterministic_gate_payload(clean), sort_keys=False,
    )

    recovered = {
        "gate_provider_step_down": {
            "failed_providers": [
                {
                    "provider": "magemin-shadow",
                    "reason": "engine_worker_timeout",
                    "disposition": "environment",
                },
                {"provider": "magemin-shadow", "reason": "composition_projected"},
                {
                    "provider": "alphamelts-diagnostic",
                    "reason": "incomplete_cooling_path",
                },
            ],
        },
    }
    assert [
        item["reason"]
        for item in deterministic_gate_payload(recovered)["gate_provider_step_down"][
            "failed_providers"
        ]
    ] == ["composition_projected", "incomplete_cooling_path"]
    assert recovered["gate_provider_step_down"]["failed_providers"][0]["disposition"] == (
        "environment"
    )

    stable = {
        "gate_provider_refusal": {
            "qualification": "out_of_domain_kress91_floor_prediction",
            "reason": "composition_projected",
        },
    }
    assert deterministic_gate_payload(stable) is stable

    timed_out = {
        "gate_provider_refusal": {
            "disposition": "environment",
            "qualification": "held_last_good_curve",
        },
        "gate_provider_step_down": {
            "failed_providers": [
                {
                    "provider": "magemin-shadow",
                    "reason": "engine_worker_timeout",
                    "disposition": "environment",
                    "engine_version": "magemin-timeout",
                },
                {
                    "provider": "alphamelts-diagnostic",
                    "reason": "incomplete_cooling_path",
                },
                {
                    "provider": "magemin-shadow",
                    "reason": "unavailable",
                    "diagnostic": {"backend_failure_disposition": "environment"},
                },
                {"provider": "magemin-shadow", "reason": "composition_projected"},
                *_timeout_escape_attempts(),
            ],
        },
    }
    stripped = _without_environment_attempts(timed_out)
    assert stripped["gate_provider_refusal"]["qualification"] == "held_last_good_curve"
    # A non-timeout disposition stays. The retry predicate does not match it.
    assert [
        item["reason"]
        for item in stripped["gate_provider_step_down"]["failed_providers"]
    ] == ["incomplete_cooling_path", "unavailable", "composition_projected"]
    with pytest.raises(AssertionError, match="environment"):
        deterministic_gate_payload(timed_out)

    floor = {
        "gate_provider_refusal": {
            "backend_failure_disposition": "environment",
            "qualification": "first_call_kress91_floor_prediction",
        },
    }
    with pytest.raises(AssertionError, match="not a golden mismatch"):
        deterministic_gate_payload(floor)

    held_sim = SimpleNamespace(
        record=SimpleNamespace(
            snapshots=[
                SimpleNamespace(
                    liquid_fraction_gate={
                        "redox": {
                            "gate_provider_refusal": {
                                "disposition": "environment",
                                "qualification": "held_last_good_curve",
                            },
                        },
                    },
                ),
            ],
        ),
    )
    with pytest.raises(AssertionError, match="held-last-good curve"):
        deterministic_gate_payload(held_sim)
    clean_sim = SimpleNamespace(
        record=SimpleNamespace(
            snapshots=[SimpleNamespace(liquid_fraction_gate={"redox": {"source": "gate"}})],
        ),
    )
    assert deterministic_gate_payload(clean_sim) is clean_sim


def test_environment_retries_drop_as_one_key():
    clean = {"source": "gate", "solidus_T_C": 1000}
    recovered = {
        **clean,
        ENVIRONMENT_RETRIES_KEY: {
            "disposition": "environment",
            "gate_provider_step_down": {
                "failed_providers": [
                    {"provider": "magemin-shadow", "reason": "engine_worker_timeout"},
                    {
                        "provider": "alphamelts-diagnostic",
                        "reason": "incomplete_cooling_path",
                    },
                ],
            },
            "alphamelts_reference_pressure": {
                "condensed_phase_reference_pressure_bar": 1,
            },
        },
    }
    assert deterministic_gate_payload(clean) is clean
    assert deterministic_gate_payload(recovered) == clean
    assert ENVIRONMENT_RETRIES_KEY in recovered


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
@pytest.mark.xdist_group("magemin_fullrun_a")
@pytest.mark.timeout(5400)
# Receipt b5022121: the capture maps matched the pin and the call then
# failed the unmarked 300 s worker budget at 370.9 s. Sibling MAGEMin
# full runs already declare the same 5400 s budget as this wall guard.
@pytest.mark.cpu_budget(5400)
def test_green_full_lunar_capture_pin(route_totals, tmp_path):
    # Current full_lunar pins: studio-2 actuals at a2299343eb, equal to the
    # d-441 owner card (b265 values). History of the earlier pins follows.
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
    # Species pins do not carry the gate refusal. Refuse a held curve here.
    deterministic_gate_payload(sim)
    assert_pin("full_lunar", route_totals, tmp_path)


@pytest.mark.parametrize("staged", [False, True], ids=["lunar_24h", "c2a_staged"])
def test_green_runner_capture_pin(staged, route_totals, tmp_path):
    # lunar_24h and c2a_staged runner_sha256 are the studio-3 actuals (pins2
    # log, tree 9666fc129 plus the de32c69ca runner regen). Lunar and Mars
    # full runs diverge between the laptop and studio-3, so these digests are
    # not a laptop re-run.
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
