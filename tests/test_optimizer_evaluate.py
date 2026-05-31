from __future__ import annotations

from dataclasses import dataclass
import math
from types import SimpleNamespace

import pytest

from simulator.backends import BackendUnavailableError
from simulator.optimize.evaluate import (
    BackendUnavailableAbort,
    EngineBugAbort,
    EvaluationInputError,
    FailureCategory,
    evaluate,
)
from simulator.optimize.recipe import RecipePatch
from simulator.state import CampaignPhase, HourSnapshot


PO2_DEFAULT = ("campaigns", "C0b_p_cleanup", "pO2_mbar_default")


PROFILE = {
    "profile_id": "clean-silica-test",
    "profile_schema_version": "profile-schema-v1",
    "objectives": [
        {"metric": "pure_silica_glass_kg", "sense": "max", "units": "kg"},
        {"metric": "oxygen_kg", "sense": "max", "units": "kg"},
        {"metric": "energy_kWh", "sense": "min", "units": "kWh"},
        {"metric": "duration_h", "sense": "min", "units": "h"},
    ],
    "run": {
        "campaign": "C0",
        "hours": 1,
        "mass_kg": 1000.0,
        "backend_name": "stub",
    },
}


@dataclass
class FakeExecutor:
    execution: object | None = None
    exc: Exception | None = None
    calls: int = 0

    def execute(self, config: object) -> object:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        assert self.execution is not None
        self.config = config
        return self.execution


class _Stage:
    def __init__(self, collected_kg: dict[str, float] | None = None) -> None:
        self.collected_kg = collected_kg or {}


class _Sim:
    def __init__(self) -> None:
        self.train = SimpleNamespace(
            stages=(
                _Stage(),
                _Stage(),
                _Stage(),
                _Stage({"SiO": 12.5}),
            )
        )
        self.record = SimpleNamespace(
            products_kg={"O2": 3.0, "Fe": 1.0},
            oxygen_stored_kg=2.5,
            oxygen_vented_kg=0.5,
            energy_total_kWh=44.0,
            total_hours=1,
        )
        self.energy_cumulative_kWh = 44.0
        self.melt = SimpleNamespace(hour=1)

    def product_ledger(self) -> dict[str, float]:
        return dict(self.record.products_kg)

    def _terminal_rump_by_species(self) -> dict[str, float]:
        return {"CaO": 2.0}

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {"stored": 2.5, "vented": 0.5, "total": 3.0}


def _snapshot(mass_balance_error_pct: float = 0.0) -> HourSnapshot:
    return HourSnapshot(
        hour=1,
        campaign=CampaignPhase.C2A,
        temperature_C=1600.0,
        mass_balance_error_pct=mass_balance_error_pct,
        knudsen_regime_summary={
            "status": "ok",
            "regime": "viscous",
            "knudsen_number": 0.001,
            "segments": [
                {
                    "name": "hot_wall",
                    "regime": "viscous",
                    "knudsen_number": 0.001,
                }
            ],
        },
    )


def _trace(*, mixed_stream: bool = False) -> SimpleNamespace:
    condensed = ({(3, "SiO"): 20.0},)
    if mixed_stream:
        condensed = ({(3, "SiO"): 19.0, (3, "Fe"): 2.0},)
    return SimpleNamespace(
        snapshots=(_snapshot(),),
        product_ledger_kg={"SiO": 95.0},
        terminal_rump_by_species_kg={"SiO2": 1.0},
        condensed_by_stage_species_delta=condensed,
        wall_deposit_by_segment_species_delta=({},),
    )


def _execution(
    *,
    status: str = "ok",
    trace: SimpleNamespace | None = None,
    mass_balance_error_pct: float = 0.0,
    snapshots: tuple[object, ...] | None = None,
    error_message: str = "",
    reason: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(),
        simulator=_Sim(),
        snapshots=(
            snapshots
            if snapshots is not None
            else (_snapshot(mass_balance_error_pct),)
        ),
        trace=trace or _trace(),
        status=status,
        error_message=error_message,
        reason=reason,
        refusal_diagnostic={},
    )


def _valid_patch() -> RecipePatch:
    return RecipePatch({PO2_DEFAULT: 9.0})


def test_mass_balance_breach_aborts_as_engine_bug_with_repro_patch() -> None:
    patch = _valid_patch()
    executor = FakeExecutor(
        _execution(mass_balance_error_pct=5.1e-12)
    )

    with pytest.raises(EngineBugAbort) as raised:
        evaluate(patch, "lunar_mare_low_ti", "fast", profile=PROFILE, executor=executor)

    assert raised.value.category is FailureCategory.ENGINE_BUG
    assert raised.value.patch == patch.validated()
    assert raised.value.cache_key


def test_empty_snapshots_abort_as_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="snapshots empty"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(_execution(snapshots=())),
        )


def test_missing_mass_balance_error_pct_aborts_as_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="snapshot 0 missing"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(_execution(snapshots=(SimpleNamespace(hour=1),))),
        )


def test_later_snapshot_mass_balance_breach_aborts_as_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="snapshot 1"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(
                _execution(snapshots=(_snapshot(0.0), _snapshot(5.1e-12)))
            ),
        )


def test_nan_closure_and_run_crash_abort_as_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="non-finite"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(_execution(mass_balance_error_pct=math.nan)),
        )

    with pytest.raises(EngineBugAbort, match="RuntimeError"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(exc=RuntimeError("boom")),
        )


def test_failed_runtime_backend_prefixed_message_is_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="oxide ledger exploded") as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(
                _execution(
                    status="failed",
                    error_message="backend failure: RuntimeError: oxide ledger exploded",
                )
            ),
        )

    assert raised.value.category is FailureCategory.ENGINE_BUG


def test_objectives_populated_only_for_feasible_runs() -> None:
    feasible = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(_execution()),
    )

    assert feasible.feasible
    assert feasible.failure_category is None
    assert feasible.objectives is not None
    assert feasible.objectives.as_mapping()["pure_silica_glass_kg"] == pytest.approx(12.5)
    assert feasible.objectives.as_mapping()["oxygen_kg"] == pytest.approx(3.0)

    infeasible = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(_execution(trace=_trace(mixed_stream=True))),
    )

    assert not infeasible.feasible
    assert infeasible.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert infeasible.objectives is None
    assert infeasible.failing_gates == ("delivered_stream_purity",)
    assert infeasible.feasibility_margins["delivered_stream_purity"].margin < 0.0


def test_missing_objective_output_on_feasible_run_aborts_as_engine_bug() -> None:
    execution = _execution()
    delattr(execution.simulator, "energy_cumulative_kWh")
    delattr(execution.simulator.record, "energy_total_kWh")

    with pytest.raises(EngineBugAbort, match="energy_total_kWh is missing") as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(execution),
        )

    assert raised.value.category is FailureCategory.ENGINE_BUG


def test_invalid_patch_rejected_before_run() -> None:
    executor = FakeExecutor(_execution())
    result = evaluate(
        RecipePatch({("campaigns", "C0", "label"): "bad"}),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=executor,
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INVALID_PATCH
    assert result.objectives is None
    assert result.eval_spec is None
    assert executor.calls == 0


def test_backend_unavailable_aborts_distinct_from_engine_bug() -> None:
    with pytest.raises(BackendUnavailableAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(exc=BackendUnavailableError("missing binary")),
        )

    assert raised.value.category is FailureCategory.BACKEND_UNAVAILABLE


def test_genuine_missing_backend_status_aborts_as_backend_unavailable() -> None:
    with pytest.raises(BackendUnavailableAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(
                _execution(
                    status="failed",
                    error_message=(
                        "backend failure: AlphaMELTS unavailable; "
                        "run install-dependencies.py"
                    ),
                )
            ),
        )

    assert raised.value.category is FailureCategory.BACKEND_UNAVAILABLE


def test_unknown_feedstock_is_input_error_not_backend_unavailable() -> None:
    with pytest.raises(EvaluationInputError, match="unknown feedstock_id"):
        evaluate(
            _valid_patch(),
            "not_a_feedstock",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(_execution()),
        )


def test_physics_refused_is_infeasible_trial_with_note() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(
            _execution(
                status="refused",
                error_message="Knudsen refusal",
                reason="free molecular",
            )
        ),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.PHYSICS_REFUSED
    assert result.objectives is None
    assert "free molecular" in result.notes


def test_evalspec_cache_key_and_scored_result_are_deterministic() -> None:
    first = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(_execution()),
    )
    second = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(_execution()),
    )

    assert first.cache_key == second.cache_key
    assert first.eval_spec == second.eval_spec
    assert first.objectives == second.objectives
    assert first.feasible == second.feasible
