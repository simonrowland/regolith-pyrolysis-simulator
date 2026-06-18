from __future__ import annotations

import random
import os

import pytest

from simulator.optimize.determinism import (
    THREAD_ENV_VARS,
    assert_deterministic,
    deterministic_result_view,
    pin_seeds,
    pin_worker_env,
)
from simulator.optimize.evalspec import (
    DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
    DEFAULT_VAPOR_PRESSURE_PROVIDER_ID,
    EvalSpec,
    cache_key,
    current_code_version,
)
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec


def _base_spec(**overrides: object) -> EvalSpec:
    data = {
        "recipe_id": "recipe-id",
        "feedstock_recipe_digest": "feedstock-recipe-digest",
        "feedstock_id": "lunar_mare_low_ti",
        "profile_id": "oxygen-yield-v1",
        "fidelity": "fast",
        "code_version": current_code_version(),
        "data_digests": {
            "setpoints": "setpoints-digest",
            "feedstocks": "feedstock-digest",
            "materials": "materials-digest",
            "vapor_pressures": "vapor-digest",
            "species_catalog": "species-catalog-digest",
            "profile": "profile-digest",
        },
        "chemistry_kernel": {
            "engine": "builtin",
            "allow_builtin_fallback": False,
            "pressure_Pa": 0.001,
        },
        "campaign": "C0",
        "hours": 24,
        "mass_kg": 1000.0,
        "additives_kg": {"CaO": 1.5},
        "track": "pyrolysis",
        "backend_name": "stub",
        "runtime_campaign_overrides": {"C0": {"hold_time_h": 1.0}},
    }
    data.update(overrides)
    return EvalSpec(**data)


def _margin(
    feasible: bool = True,
    *,
    gate: str = "delivered_stream_purity",
    margin: float | None = None,
    observed: float | None = None,
) -> GateMargin:
    return GateMargin(
        gate=gate,
        feasible=feasible,
        margin=margin if margin is not None else (0.25 if feasible else -0.25),
        threshold=ThresholdSpec(
            id=f"{gate}_min",
            value=0.95,
            units="fraction",
            source="profile",
            source_ref="test profile",
        ),
        observed=observed if observed is not None else (0.98 if feasible else 0.90),
        detail="test margin",
    )


def _objectives(oxygen: float = 10.0) -> ObjectiveVector:
    return ObjectiveVector(
        (
            ObjectiveValue("oxygen_kg", "maximize", oxygen, "kg", ordinal=0),
            ObjectiveValue("duration_h", "minimize", 24.0, "h", ordinal=1),
        )
    )


def _result(
    spec: EvalSpec,
    *,
    oxygen: float = 10.0,
    feasible: bool = True,
    trace: dict[str, object] | None = None,
    product_summary: dict[str, object] | None = None,
    candidate_id: str | None = "candidate-a",
    cache_key_value: str | None = None,
    notes: tuple[str, ...] = (),
    margins: dict[str, GateMargin] | None = None,
) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec) if cache_key_value is None else cache_key_value,
        feasible=feasible,
        failure_category=None if feasible else FailureCategory.PHYSICS_REFUSED,
        objectives=_objectives(oxygen) if feasible else None,
        feasibility_margins=margins or {"delivered_stream_purity": _margin(feasible)},
        failing_gates=() if feasible else ("delivered_stream_purity",),
        run_reference=RunReference(
            status="ok",
            trace=trace
            or {
                "campaign_hours": 24.0,
                "products": {"oxygen_kg": oxygen},
                "ledger": {"O": oxygen / 16.0},
            },
            product_summary=product_summary or {"oxygen_kg": oxygen},
        ),
        notes=notes,
    )


def test_evalspec_cache_key_splits_vapor_provider_mode() -> None:
    live_vaporock = _base_spec(
        vapor_pressure_provider_id=DEFAULT_VAPOR_PRESSURE_PROVIDER_ID,
        vapor_pressure_fallback_provider_id=DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
        allow_fallback_vapor=False,
        force_builtin_vapor_pressure=False,
    )
    forced_builtin = _base_spec(
        vapor_pressure_provider_id=DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
        vapor_pressure_fallback_provider_id=DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
        allow_fallback_vapor=True,
        force_builtin_vapor_pressure=True,
    )
    identical_forced_builtin = _base_spec(
        vapor_pressure_provider_id=DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
        vapor_pressure_fallback_provider_id=DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
        allow_fallback_vapor=True,
        force_builtin_vapor_pressure=True,
    )
    provider_only_changed = _base_spec(
        vapor_pressure_provider_id=DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
        vapor_pressure_fallback_provider_id=DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
        allow_fallback_vapor=False,
        force_builtin_vapor_pressure=False,
    )
    fallback_only_changed = _base_spec(
        vapor_pressure_provider_id=DEFAULT_VAPOR_PRESSURE_PROVIDER_ID,
        vapor_pressure_fallback_provider_id=DEFAULT_VAPOR_PRESSURE_FALLBACK_PROVIDER_ID,
        allow_fallback_vapor=True,
        force_builtin_vapor_pressure=False,
    )

    assert cache_key(live_vaporock) != cache_key(forced_builtin)
    assert cache_key(live_vaporock) != cache_key(provider_only_changed)
    assert cache_key(live_vaporock) != cache_key(fallback_only_changed)
    assert cache_key(forced_builtin) == cache_key(identical_forced_builtin)


def test_deterministic_eval_repeats_identical_view() -> None:
    spec = _base_spec()

    def evaluate_fn(eval_spec: EvalSpec) -> ScoredResult:
        return _result(eval_spec)

    results = assert_deterministic(evaluate_fn, spec, repeats=2)

    assert len(results) == 2
    assert deterministic_result_view(results[0]) == deterministic_result_view(results[1])


def test_deterministic_result_view_handles_clean_infinite_margin() -> None:
    spec = _base_spec()
    result = _result(
        spec,
        margins={
            "delivered_stream_purity": _margin(),
            "coating": _margin(
                gate="coating",
                margin=float("inf"),
                observed=float("inf"),
            ),
        },
    )

    view = deterministic_result_view(result)

    assert '"+inf"' in view


def test_volatile_metadata_stripped_but_substantive_fields_kept() -> None:
    spec = _base_spec()
    first = _result(
        spec,
        trace={
            "campaign_hours": 24.0,
            "products": {"oxygen_kg": 10.0},
            "created_at": "2026-05-31T00:00:00Z",
            "uuid": "run-a",
            "wall_time_s": 1.1,
            "host": "worker-a",
            "output_path": "/tmp/run-a.json",
            "output_dir": "/tmp/run-a",
            "report_path": "/tmp/run-a/report.md",
            "log_file": "/tmp/run-a/eval.log",
        },
    )
    second = _result(
        spec,
        trace={
            "campaign_hours": 24.0,
            "products": {"oxygen_kg": 10.0},
            "created_at": "2026-05-31T00:00:02Z",
            "uuid": "run-b",
            "wall_time_s": 2.2,
            "host": "worker-b",
            "output_path": "/tmp/run-b.json",
            "output_dir": "/tmp/run-b",
            "report_path": "/tmp/run-b/report.md",
            "log_file": "/tmp/run-b/eval.log",
        },
    )

    assert deterministic_result_view(first) == deterministic_result_view(second)
    assert deterministic_result_view(first) != deterministic_result_view(
        _result(spec, oxygen=11.0, trace={"campaign_hours": 24.0})
    )
    assert deterministic_result_view(first) != deterministic_result_view(
        _result(
            spec,
            trace={"campaign_hours": 25.0, "products": {"oxygen_kg": 10.0}},
        )
    )
    assert deterministic_result_view(first) != deterministic_result_view(
        _result(spec, feasible=False, trace={"campaign_hours": 24.0})
    )


def test_liquid_fraction_path_drift_is_substantive() -> None:
    spec = _base_spec()
    first = _result(
        spec,
        trace={
            "campaign_hours": 24.0,
            "liquid_fraction_path": [
                {"temperature_C": 1450.0, "liquid_fraction": 0.72},
            ],
        },
    )
    second = _result(
        spec,
        trace={
            "campaign_hours": 24.0,
            "liquid_fraction_path": [
                {"temperature_C": 1450.0, "liquid_fraction": 0.41},
            ],
        },
    )
    assert deterministic_result_view(first) != deterministic_result_view(second)

    first_summary = _result(
        spec,
        product_summary={
            "oxygen_kg": 10.0,
            "liquid_fraction_path": [{"temperature_C": 1450.0, "liquid_fraction": 0.72}],
        },
    )
    second_summary = _result(
        spec,
        product_summary={
            "oxygen_kg": 10.0,
            "liquid_fraction_path": [{"temperature_C": 1450.0, "liquid_fraction": 0.41}],
        },
    )
    assert deterministic_result_view(first_summary) != deterministic_result_view(
        second_summary
    )


def test_candidate_id_is_nonsubstantive_strategy_identity() -> None:
    spec = _base_spec()

    assert deterministic_result_view(
        _result(spec, candidate_id="strategy-a-0001")
    ) == deterministic_result_view(_result(spec, candidate_id="strategy-b-0042"))


def test_cache_key_and_eval_spec_are_substantive() -> None:
    spec = _base_spec()

    assert deterministic_result_view(_result(spec)) != deterministic_result_view(
        _result(spec, cache_key_value="accidental-cache-key-drift")
    )
    assert deterministic_result_view(_result(spec)) != deterministic_result_view(
        _result(_base_spec(hours=48))
    )


def test_notes_are_substantive_branch_reasons() -> None:
    spec = _base_spec()

    assert deterministic_result_view(
        _result(spec, notes=("physics refused: SiO vapor pressure gate",))
    ) != deterministic_result_view(
        _result(spec, notes=("invalid patch: electrolysis current out of range",))
    )
    assert deterministic_result_view(_result(spec, notes=("first", "second"))) != (
        deterministic_result_view(_result(spec, notes=("second", "first")))
    )


def test_probe_catches_nondeterministic_eval_with_diff() -> None:
    spec = _base_spec()
    state = {"count": 0}

    def evaluate_fn(eval_spec: EvalSpec) -> ScoredResult:
        state["count"] += 1
        return _result(eval_spec, oxygen=10.0 + state["count"])

    with pytest.raises(AssertionError, match="nondeterministic evaluation result"):
        assert_deterministic(evaluate_fn, spec, repeats=2)


def test_pin_worker_env_sets_single_thread_vars_idempotently(monkeypatch) -> None:
    for name in THREAD_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    pin_worker_env()
    pin_worker_env()

    assert {name: "1" for name in THREAD_ENV_VARS} == {
        name: os.environ[name]
        for name in THREAD_ENV_VARS
    }


def test_pin_seeds_is_deterministic() -> None:
    pin_seeds(123)
    first = random.random()
    pin_seeds(123)
    assert random.random() == first

    try:
        import numpy as np
    except ImportError:
        return

    pin_seeds(123)
    first_numpy = float(np.random.random())
    pin_seeds(123)
    assert float(np.random.random()) == first_numpy
