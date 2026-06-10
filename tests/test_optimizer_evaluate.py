from __future__ import annotations

from dataclasses import dataclass
import math
from types import SimpleNamespace

import pytest

from engines.alphamelts import AlphaMELTSProvider
from simulator.accounting.ledger import AtomLedger
from simulator.backends import BackendUnavailableError
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ChemistryKernel,
    ProposalRejected,
    ProviderRegistry,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.liquidus import LiquidusSolidusResult
from simulator.optimize.evaluate import (
    BackendUnavailableAbort,
    EngineBugAbort,
    EvaluationInputError,
    FailureCategory,
    evaluate,
)
from simulator.optimize.objective import objective_definitions
from simulator.optimize.profiles import ProfileValidationError
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.results_store import ResultStore
from simulator.reduced_real_determinism import PT0NonFinitePayload
from simulator.runner import RunnerError
from simulator.state import CampaignPhase, HourSnapshot


PO2_DEFAULT = ("campaigns", "C0b_p_cleanup", "pO2_mbar_default")


PROFILE = {
    "profile_id": "clean-silica-test",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": "lunar_mare_low_ti",
    "objectives": [
        {
            "metric": "pure_silica_glass_kg",
            "sense": "max",
            "units": "kg",
            "weight": 0.4,
            "rationale": "test silica objective evidence",
        },
        {
            "metric": "oxygen_kg",
            "sense": "max",
            "units": "kg",
            "weight": 0.3,
            "rationale": "test oxygen objective evidence",
        },
        {
            "metric": "energy_kWh",
            "sense": "min",
            "units": "kWh",
            "weight": 0.15,
            "rationale": "test energy objective evidence",
        },
        {
            "metric": "duration_h",
            "sense": "min",
            "units": "h",
            "weight": 0.15,
            "rationale": "test duration objective evidence",
        },
    ],
    "constraints": {"gates": ["delivered_stream_purity"]},
    "run": {
        "campaign": "C0",
        "hours": 1,
        "mass_kg": 1000.0,
        "backend_name": "stub",
    },
    "fidelities": {"fast": {"backend_name": "stub", "hours": 1}},
    "seed_recipes": [
        {
            "id": "evaluate-c0-seed",
            "source_campaign": "C0",
            "patch": {"campaigns": {"C0": {"temp_range_C": [900, 950]}}},
        }
    ],
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


class _EvalLedger:
    def mol_by_account(self, account: str | None = None):
        balances = {"process.cleaned_melt": {"CaO": 1.0}}
        if account is None:
            return {key: dict(value) for key, value in balances.items()}
        return dict(balances.get(account, {}))


class _Sim:
    def __init__(
        self,
        *,
        backend_diagnostics: dict[str, object] | None = None,
        freeze_curve: dict[str, object] | None = None,
    ) -> None:
        self.atom_ledger = _EvalLedger()
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
        self._last_backend_diagnostics = backend_diagnostics or {}
        self._freeze_curve = freeze_curve
        self.kernel_curve_requests: list[dict[str, object]] = []

    def product_ledger(self) -> dict[str, float]:
        return dict(self.record.products_kg)

    def _terminal_rump_by_species(self) -> dict[str, float]:
        return {"CaO": 2.0}

    def _oxygen_terminal_partition_kg(self) -> dict[str, float]:
        return {"stored": 2.5, "vented": 0.5, "total": 3.0}

    def _freeze_gate_curve_from_kernel_liquidus(
        self,
        reasons: list[str],
        *,
        fO2_log: float,
        temperature_C: float | None = None,
        pressure_bar: float | None = None,
        composition_mol_by_account: dict[str, dict[str, float]] | None = None,
        allow_parametric: bool = False,
    ) -> dict[str, object] | None:
        self.kernel_curve_requests.append(
            {
                "fO2_log": fO2_log,
                "temperature_C": temperature_C,
                "pressure_bar": pressure_bar,
                "composition_mol_by_account": composition_mol_by_account,
                "allow_parametric": allow_parametric,
            }
        )
        if self._freeze_curve is None:
            reasons.append("test kernel liquidus unavailable")
            return None
        return dict(self._freeze_curve)

    @staticmethod
    def _interpolate_freeze_gate_curve(
        curve: dict[str, object],
        temperature_C: float,
    ) -> float:
        solidus_T_C = float(curve["solidus_T_C"])
        liquidus_T_C = float(curve["liquidus_T_C"])
        if temperature_C <= solidus_T_C:
            return 0.0
        if temperature_C >= liquidus_T_C:
            return 1.0
        return (temperature_C - solidus_T_C) / (liquidus_T_C - solidus_T_C)


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
        terminal_rump_by_species_kg={"CaO": 2.0},
        condensed_by_stage_species_delta=condensed,
        wall_deposit_by_segment_species_kg={},
        wall_zone_by_segment={},
        wall_deposit_by_segment_species_delta=({},),
    )


def test_raw_profile_mapping_unknown_key_raises_before_run() -> None:
    bad_profile = {**PROFILE, "brnach": "one"}
    executor = FakeExecutor(execution=_execution())

    with pytest.raises(ProfileValidationError, match="unknown profile key 'brnach'"):
        evaluate(
            RecipePatch({}),
            "lunar_mare_low_ti",
            "fast",
            profile=bad_profile,
            executor=executor,
        )

    assert executor.calls == 0


def _execution(
    *,
    status: str = "ok",
    trace: SimpleNamespace | None = None,
    mass_balance_error_pct: float = 0.0,
    snapshots: tuple[object, ...] | None = None,
    error_message: str = "",
    reason: str = "",
    per_hour: tuple[object, ...] | None = None,
    backend_status: str | None = None,
    backend_authoritative: bool | None = None,
    backend_diagnostics: dict[str, object] | None = None,
    freeze_curve: dict[str, object] | None = None,
) -> SimpleNamespace:
    per_hour_entries = per_hour
    if per_hour_entries is None and backend_status is not None:
        per_hour_entries = ({"backend_status": backend_status},)
    return SimpleNamespace(
        session=SimpleNamespace(),
        simulator=_Sim(
            backend_diagnostics=backend_diagnostics,
            freeze_curve=freeze_curve,
        ),
        snapshots=(
            snapshots
            if snapshots is not None
            else (_snapshot(mass_balance_error_pct),)
        ),
        trace=trace or _trace(),
        per_hour=per_hour_entries or (),
        backend_status=backend_status,
        backend_authoritative=backend_authoritative,
        status=status,
        error_message=error_message,
        reason=reason,
        refusal_diagnostic={},
    )


def _valid_patch() -> RecipePatch:
    return RecipePatch({PO2_DEFAULT: 9.0})


def _crash_diagnostics(temperature_C: float = 1100.0) -> dict[str, object]:
    return {
        "backend_status": "out_of_domain",
        "out_of_domain_crash_point": {
            "temperature_C": temperature_C,
            "pressure_bar": 1.0e-6,
            "fO2_log": -9.0,
            "composition_wt_pct": {"SiO2": 55.0, "CaO": 45.0},
            "composition_mol": {"SiO2": 1.0, "CaO": 1.0},
        },
    }


def _kernel_curve(
    *,
    source: str = "liquidus_solidus:kernel:composition_derived",
    composition_derived: bool = True,
) -> dict[str, object]:
    return {
        "source": source,
        "composition_derived": composition_derived,
        "solidus_T_C": 1200.0,
        "liquidus_T_C": 1400.0,
        "path": ((1200.0, 0.0), (1400.0, 1.0)),
    }


def _composition_eval_profile(
    pool: str,
    *,
    target_id: str = "composition-eval-test",
    oxides: dict[str, dict[str, float]] | None = None,
) -> dict:
    profile = {
        **PROFILE,
        "profile_id": f"{target_id}-profile",
        "objectives": [
            {
                "type": "composition_target",
                "id": target_id,
                "metric": f"composition_target:{target_id}",
                "sense": "maximize",
                "units": "score_0_1",
                "weight": 1.0,
                "rationale": "test composition target objective",
                "target": {
                    "pool": pool,
                    "species_vector": {"Ca": "retain"},
                    "composition_window": {
                        "pool": pool,
                        "basis": "oxide_wt_pct",
                        "mode": "hard_window",
                        "oxides": oxides
                        or {"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
                    },
                    "maturity": {"mode": "campaign_hours", "campaign": "C2B", "hours": 24},
                    "constraints": {
                        "coating_min_campaigns_to_resinter": "profile_default",
                        "furnace_T_max_C": "profile_or_study_constraint",
                    },
                    "score_weights": {"extraction": 0.0, "composition": 1.0},
                },
            }
        ],
    }
    return profile


def test_pt0_nonfinite_payload_exception_is_candidate_failure() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(
            exc=PT0NonFinitePayload(
                "non-finite value in PT-0 payload at $.SCSS_ppm: inf"
            )
        ),
    )

    assert result.feasible is False
    assert result.failure_category is FailureCategory.NON_FINITE_PAYLOAD
    assert result.failing_gates == ("non_finite_payload",)
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "ok"
    assert any("CALC_BUG" in note for note in result.notes)


def test_pt0_nonfinite_failed_run_is_candidate_failure() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(
            _execution(
                status="failed",
                error_message=(
                    "PT0NonFinitePayload: non-finite value in PT-0 payload "
                    "at $.SCSS_ppm: inf"
                ),
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    assert result.feasible is False
    assert result.failure_category is FailureCategory.NON_FINITE_PAYLOAD
    assert result.run_reference is not None
    assert result.run_reference.error_message.startswith("PT0NonFinitePayload")


def test_proposal_rejected_direct_exception_is_invalid_recipe() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(
            exc=ProposalRejected(
                "insufficient available 'FeO' in normal account "
                "'process.cleaned_melt': balance would be -7.87e-05 kg"
            )
        ),
    )

    assert result.feasible is False
    assert result.failure_category is FailureCategory.INVALID_RECIPE
    assert result.failing_gates == ("inventory_overdraw",)
    assert result.feasibility_margins["inventory_overdraw"].observed == pytest.approx(
        7.87e-05
    )
    assert any("overdraw_kg=7.87e-05" in note for note in result.notes)


def test_proposal_rejected_runner_paths_are_invalid_recipe() -> None:
    for executor in (
        FakeExecutor(
            exc=RunnerError(
                "ProposalRejected: insufficient available 'Cr2O3' in normal "
                "account 'process.cleaned_melt': balance would be -0.125 kg"
            )
        ),
        FakeExecutor(
            _execution(
                status="failed",
                error_message=(
                    "ProposalRejected: insufficient available 'Al2O3' in normal "
                    "account 'process.cleaned_melt': balance would be -2.5 kg"
                ),
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    ):
        result = evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=executor,
        )

        assert result.feasible is False
        assert result.failure_category is FailureCategory.INVALID_RECIPE
        assert result.run_reference is not None
        assert result.run_reference.backend_status == "ok"


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


def test_objective_definitions_keep_profile_order_as_ordinal() -> None:
    definitions = objective_definitions(PROFILE)

    assert [(definition.metric, definition.sense, definition.ordinal) for definition in definitions] == [
        ("pure_silica_glass_kg", "maximize", 0),
        ("oxygen_kg", "maximize", 1),
        ("energy_kWh", "minimize", 2),
        ("duration_h", "minimize", 3),
    ]


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


def test_composition_target_missing_pool_aborts_as_engine_bug() -> None:
    execution = _execution()
    execution.simulator.train.stages[3].collected_kg = {}
    execution.trace.condensed_by_stage_species_delta = ({},)
    profile = _composition_eval_profile(
        "captured_stage_3_silica",
        target_id="pc-missing-pool",
    )
    profile["constraints"] = {"gates": ["coating"]}

    with pytest.raises(EngineBugAbort, match="captured_stage_3_silica") as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=profile,
            executor=FakeExecutor(execution),
        )

    assert raised.value.category is FailureCategory.ENGINE_BUG


def test_composition_target_terminal_rump_unearned_is_infeasible() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_composition_eval_profile(
            "terminal_rump_earned",
            target_id="pc-ceramic-test",
            oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        ),
        executor=FakeExecutor(_execution()),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert result.objectives is None
    assert result.failing_gates == ("rump_terminal",)
    assert "rump_terminal_unproven" in result.notes


def test_composition_target_forces_coating_gate_for_arbitrary_target_id() -> None:
    trace = _trace()
    delattr(trace, "wall_deposit_by_segment_species_delta")
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_composition_eval_profile(
            "residual_rump_at_stop",
            target_id="glass-clear-post-fe-v1",
        ),
        executor=FakeExecutor(_execution(trace=trace)),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert result.objectives is None
    assert result.failing_gates == ("coating",)


def test_composition_target_require_coating_gate_false_skips_coating_gate() -> None:
    trace = _trace()
    delattr(trace, "wall_deposit_by_segment_species_delta")
    profile = _composition_eval_profile(
        "residual_rump_at_stop",
        target_id="glass-clear-post-fe-v1",
    )
    profile["objectives"][0]["target"]["require_coating_gate"] = False

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(_execution(trace=trace)),
    )

    assert result.feasible
    assert result.failing_gates == ()


def test_legacy_metric_profile_does_not_force_coating_gate() -> None:
    trace = _trace()
    delattr(trace, "wall_deposit_by_segment_species_delta")

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(_execution(trace=trace)),
    )

    assert result.feasible
    assert result.failing_gates == ()


def test_invalid_patch_rejected_before_run() -> None:
    executor = FakeExecutor(_execution(backend_status="unavailable"))
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


def test_real_backend_missing_backend_status_aborts_as_backend_unavailable() -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    with pytest.raises(BackendUnavailableAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "high",
            profile=real_profile,
            executor=FakeExecutor(_execution()),
        )

    assert raised.value.category is FailureCategory.BACKEND_UNAVAILABLE
    assert "backend_status missing" in str(raised.value)


def test_real_backend_unavailable_backend_status_aborts_as_backend_unavailable() -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    with pytest.raises(BackendUnavailableAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "high",
            profile=real_profile,
            executor=FakeExecutor(_execution(backend_status="unavailable")),
        )

    assert raised.value.category is FailureCategory.BACKEND_UNAVAILABLE
    assert "backend_status='unavailable'" in str(raised.value)


def test_real_backend_out_of_domain_status_is_infeasible_result() -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=real_profile,
        executor=FakeExecutor(_execution(backend_status="out_of_domain")),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.OUT_OF_DOMAIN
    assert result.objectives is None
    assert result.failing_gates == ("backend_domain",)
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "out_of_domain"
    margin = result.feasibility_margins["backend_domain"]
    assert margin.observed == pytest.approx(0.0)
    assert math.isfinite(margin.margin)
    assert "rump_terminal: not_earned reason=missing_crash_point" in result.notes


def test_real_backend_out_of_domain_subsolidus_rump_terminal_is_scored_success() -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    execution = _execution(
        backend_status="out_of_domain",
        backend_diagnostics=_crash_diagnostics(temperature_C=1100.0),
        freeze_curve=_kernel_curve(),
    )
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=real_profile,
        executor=FakeExecutor(execution),
    )

    requests = execution.simulator.kernel_curve_requests
    assert requests, "rump earning must request a kernel curve"
    earned_request = requests[-1]
    assert earned_request["allow_parametric"] is False
    assert earned_request["temperature_C"] == pytest.approx(1100.0)
    assert earned_request["pressure_bar"] == pytest.approx(1.0e-6)
    assert earned_request["fO2_log"] == pytest.approx(-9.0)
    overrides = earned_request["composition_mol_by_account"]
    assert overrides, "crash composition must be passed as account overrides"
    assert any("SiO2" in mols for mols in overrides.values())

    assert result.feasible
    assert result.failure_category is None
    assert result.objectives is not None
    assert "rump_terminal" in result.feasibility_margins
    assert any("earned_by=kernel_liquidus" in note for note in result.notes)
    assert result.run_reference is not None
    assert result.run_reference.product_summary["product_bins"][
        "refractory_ceramic_rump"
    ]["kg"] == pytest.approx(2.0)
    trace = result.run_reference.trace
    assert trace["rump_terminal"]["status"] == "earned"
    assert trace["rump_terminal"]["curve_source"] == (
        "liquidus_solidus:kernel:composition_derived"
    )
    assert trace["rump_terminal"]["composition_derived"] is True
    assert trace["rump_terminal"]["proof_inputs"]["T_crash_C"] == pytest.approx(
        1100.0
    )
    assert "composition_digest" in trace["rump_terminal"]["proof_inputs"]
    assert trace["out_of_domain_crash_point"]["temperature_C"] == pytest.approx(1100.0)
    assert trace["terminal_rump_by_species_kg"] == {"CaO": 2.0}


def test_kernel_liquidus_account_overrides_reach_alphamelts_provider() -> None:
    class RecordingAlphaMELTSBackend(AlphaMELTSBackend):
        def __init__(self) -> None:
            super().__init__()
            self._mode = "subprocess"
            self.finder_calls: list[dict[str, object]] = []

        def get_engine_version(self) -> str:
            return "fake-alphamelts subprocess"

        def find_liquidus_solidus(self, **kwargs: object) -> LiquidusSolidusResult:
            self.finder_calls.append(dict(kwargs))
            return LiquidusSolidusResult(
                liquidus_T_C=1400.0,
                solidus_T_C=1200.0,
                liquid_fraction=1.0,
                status="ok",
            )

    backend = RecordingAlphaMELTSBackend()
    provider = AlphaMELTSProvider(backend=backend)
    registry = ProviderRegistry()
    registry.register(provider, [ChemistryIntent.SILICATE_LIQUIDUS])
    kernel = ChemistryKernel(
        ledger=AtomLedger(
            initial_balances={"process.cleaned_melt": {"SiO2": 99.0}}
        ),
        registry=registry,
        species_formula_registry={},
    )

    overrides = {"process.cleaned_melt": {"SiO2": 1.0, "CaO": 2.0}}
    result = kernel.dispatch(
        ChemistryIntent.SILICATE_LIQUIDUS,
        temperature_C=1100.0,
        pressure_bar=1.0e-6,
        fO2_log=-9.0,
        account_mol_overrides=overrides,
    )

    assert result.status == "ok"
    assert backend.finder_calls
    provider_request = backend.finder_calls[-1]["composition_mol_by_account"]
    assert provider_request == overrides


@pytest.mark.parametrize(
    "curve_source",
    (
        "liquidus_solidus:kernel",
        "liquidus_solidus:kernel:parametric_dry_silicate_lower_bound",
    ),
)
def test_real_backend_out_of_domain_default_or_parametric_curve_cannot_earn(
    curve_source: str,
) -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=real_profile,
        executor=FakeExecutor(
            _execution(
                backend_status="out_of_domain",
                backend_diagnostics=_crash_diagnostics(temperature_C=1100.0),
                freeze_curve=_kernel_curve(
                    source=curve_source,
                    composition_derived=False,
                ),
            )
        ),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.OUT_OF_DOMAIN
    assert result.objectives is None
    assert any(
        "rump_terminal_unproven: kernel curve not composition-derived" in note
        for note in result.notes
    )
    assert result.run_reference is not None
    trace = result.run_reference.trace
    assert trace["rump_terminal"]["status"] == "not_earned"
    assert trace["rump_terminal"]["reason"] == "rump_terminal_unproven"
    assert trace["rump_terminal"]["curve_source"] == curve_source
    assert trace["rump_terminal"]["composition_derived"] is False
    assert trace["rump_terminal"]["proof_inputs"]["T_crash_C"] == pytest.approx(
        1100.0
    )


def test_real_backend_out_of_domain_same_temperature_keys_on_curve_provenance() -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    parametric = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=real_profile,
        executor=FakeExecutor(
            _execution(
                backend_status="out_of_domain",
                backend_diagnostics=_crash_diagnostics(temperature_C=1100.0),
                freeze_curve=_kernel_curve(
                    source=(
                        "liquidus_solidus:kernel:"
                        "parametric_dry_silicate_lower_bound"
                    ),
                    composition_derived=False,
                ),
            )
        ),
    )
    derived = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=real_profile,
        executor=FakeExecutor(
            _execution(
                backend_status="out_of_domain",
                backend_diagnostics=_crash_diagnostics(temperature_C=1100.0),
                freeze_curve=_kernel_curve(),
            )
        ),
    )

    assert not parametric.feasible
    assert parametric.failure_category is FailureCategory.OUT_OF_DOMAIN
    assert derived.feasible
    assert derived.failure_category is None


def test_real_backend_out_of_domain_above_solidus_disagrees_and_skips() -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=real_profile,
        executor=FakeExecutor(
            _execution(
                backend_status="out_of_domain",
                backend_diagnostics=_crash_diagnostics(temperature_C=1300.0),
                freeze_curve=_kernel_curve(),
            )
        ),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.OUT_OF_DOMAIN
    assert result.objectives is None
    assert any("kernel_liquidus_disagree" in note for note in result.notes)
    assert result.run_reference is not None
    assert result.run_reference.trace["rump_terminal"]["status"] == "not_earned"
    assert result.run_reference.trace["rump_terminal"]["liquid_fraction"] == pytest.approx(
        0.5
    )


def test_out_of_domain_crash_provenance_round_trips_results_store(tmp_path) -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=real_profile,
        executor=FakeExecutor(
            _execution(
                backend_status="out_of_domain",
                backend_diagnostics=_crash_diagnostics(temperature_C=1100.0),
                freeze_curve=_kernel_curve(),
            )
        ),
    )
    assert result.eval_spec is not None
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=result.eval_spec.code_version,
        current_data_digests=result.eval_spec.data_digests,
    )

    store.store(result.eval_spec, result, created_at="2026-06-09T00:00:00Z")
    loaded = store.lookup(result.eval_spec)

    assert loaded is not None
    assert loaded.feasible
    assert loaded.run_reference is not None
    trace = loaded.run_reference.trace
    assert trace["rump_terminal"]["status"] == "earned"
    assert trace["out_of_domain_crash_point"]["composition_mol"]["CaO"] == pytest.approx(
        1.0
    )


def test_real_backend_ok_but_non_authoritative_aborts_as_backend_unavailable() -> None:
    real_profile = {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    with pytest.raises(BackendUnavailableAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "high",
            profile=real_profile,
            executor=FakeExecutor(
                _execution(
                    backend_status="ok",
                    backend_authoritative=False,
                )
            ),
        )

    assert raised.value.category is FailureCategory.BACKEND_UNAVAILABLE
    assert "backend_authoritative is not True" in str(raised.value)


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


def test_composition_target_evalspec_carries_target_digest() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_composition_eval_profile(
            "residual_rump_at_stop",
            target_id="pc-glass-test",
            oxides={"Fe2O3": {"tier": "clear_container"}},
        ),
        executor=FakeExecutor(_execution()),
    )

    assert result.eval_spec is not None
    assert result.eval_spec.target_spec_id == "pc-glass-test"
    assert result.eval_spec.target_spec_digest
    assert result.eval_spec.target_maturity["campaign"] == "C2B"
    row = result.eval_spec.target_provenance["composition_window"]["oxides"]["Fe2O3"]
    assert row["tier"] == "clear_container"
    assert row["needs_experiment"] is True


def test_require_coating_gate_toggle_changes_target_digest_and_cache_key() -> None:
    default_profile = _composition_eval_profile(
        "residual_rump_at_stop",
        target_id="glass-clear-post-fe-v1",
    )
    opt_out_profile = _composition_eval_profile(
        "residual_rump_at_stop",
        target_id="glass-clear-post-fe-v1",
    )
    opt_out_profile["objectives"][0]["target"]["require_coating_gate"] = False

    default_result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=default_profile,
        executor=FakeExecutor(_execution()),
    )
    opt_out_result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=opt_out_profile,
        executor=FakeExecutor(_execution()),
    )

    assert default_result.eval_spec is not None
    assert opt_out_result.eval_spec is not None
    assert default_result.eval_spec.target_spec_digest != opt_out_result.eval_spec.target_spec_digest
    assert default_result.cache_key != opt_out_result.cache_key


def test_cached_real_profile_builds_honest_evalspec_and_cache_config(
    tmp_path,
) -> None:
    cache_config = {
        "db_path": str(tmp_path / "pt1-cache.db"),
        "miss_policy": "fail-loud",
        "authorized_backend_name": "alphamelts",
        "authorized_backend_version": "test-version",
    }
    profile = {
        **PROFILE,
        "fidelities": {
            "high": {
                "backend_name": "cached-real",
                "hours": 1,
                "reduced_real_cache": cache_config,
            }
        },
    }
    executor = FakeExecutor(_execution(backend_status="ok", backend_authoritative=True))

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=profile,
        executor=executor,
    )

    assert result.eval_spec is not None
    assert result.eval_spec.backend_name == "cached-real"
    assert executor.config.backend_name == "cached-real"
    assert executor.config.reduced_real_cache == cache_config


def test_stub_fidelity_drops_inherited_cached_real_cache_config(tmp_path) -> None:
    cache_config = {
        "db_path": str(tmp_path / "pt1-cache.db"),
        "miss_policy": "fail-loud",
        "authorized_backend_name": "alphamelts",
        "authorized_backend_version": "test-version",
    }
    profile = {
        **PROFILE,
        "run": {
            **PROFILE["run"],
            "backend_name": "cached-real",
            "reduced_real_cache": cache_config,
        },
        "fidelities": {"fast": {"backend_name": "stub", "hours": 1}},
    }
    executor = FakeExecutor(_execution(backend_status="ok"))

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=executor,
    )

    assert result.eval_spec is not None
    assert result.eval_spec.backend_name == "stub"
    assert executor.config.backend_name == "stub"
    assert executor.config.reduced_real_cache is None


def test_cached_real_fidelity_inherits_run_level_cache_config(tmp_path) -> None:
    cache_config = {
        "db_path": str(tmp_path / "pt1-cache.db"),
        "miss_policy": "fail-loud",
        "authorized_backend_name": "alphamelts",
        "authorized_backend_version": "test-version",
    }
    profile = {
        **PROFILE,
        "run": {
            **PROFILE["run"],
            "backend_name": "cached-real",
            "reduced_real_cache": cache_config,
        },
        "fidelities": {"high": {"backend_name": "cached-real", "hours": 1}},
    }
    executor = FakeExecutor(_execution(backend_status="ok", backend_authoritative=True))

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=profile,
        executor=executor,
    )

    assert result.eval_spec is not None
    assert result.eval_spec.backend_name == "cached-real"
    assert executor.config.backend_name == "cached-real"
    assert executor.config.reduced_real_cache == cache_config
