from __future__ import annotations

from dataclasses import dataclass, replace
import math
from types import SimpleNamespace

import pytest

from engines.alphamelts import AlphaMELTSProvider
from engines.alphamelts.parser import diagnostics_to_equilibrium
from engines.alphamelts.result import LiquidusDiagnostics
from engines.builtin.melt_effect_adjustment import CertifiedPointRefusedError
from engines.builtin.vapor_pressure import (
    VaporPressureRangeError,
    _pow10_pressure_or_raise,
)
import simulator.optimize.evaluate as evaluate_module
from simulator.accounting.ledger import AtomLedger
from simulator.backends import BackendSelectionPolicy, BackendUnavailableError
from simulator.campaigns import CampaignPressureSetpointRefusal
from simulator.chemistry.kernel import (
    ChemistryIntent,
    ChemistryKernel,
    ProposalRejected,
    ProviderRegistry,
)
from simulator.condensation import KnudsenRegimeRefusal
from simulator.config import load_config_bundle
from simulator.electrolysis import (
    MRE_MULTI_OXIDE_PARTITION_REFUSAL,
    MRE_RAW_MARGIN_REFUSAL,
)
from simulator.fidelity_vocabulary import (
    FidelityVocabularyTranslationError,
    UnknownFidelityVocabularyTokenError,
)
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.liquidus import LiquidusSolidusResult
from simulator.optimize.evaluate import (
    BackendUnavailableAbort,
    EngineBugAbort,
    EvaluationAbort,
    EvaluationInputError,
    FailureCategory,
    MASS_BALANCE_ABORT_PCT,
    ZERO_INPUT_BASIS_BREACH,
    _composition_target_constraints,
    evaluate,
)
from simulator.optimize.evalspec import DEFAULT_VAPOR_PRESSURE_PROVIDER_ID, cache_key
from simulator.optimize.objective import (
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    objective_definitions,
)
from simulator.optimize.physics import PhysicsConstraintSet
from simulator.optimize.product_pools import forbidden_gates_for_pool
from simulator.optimize.profiles import ProfileValidationError
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.results_store import ResultStore
from simulator.pumping_cost import MARS_DATUM_AMBIENT_PA, estimate_subambient_pump_cost
from simulator.reduced_real_determinism import PT0NonFinitePayload
from simulator.run_executor import RunExecutor
from simulator.runner import RunnerError, _force_builtin_vapor_pressure
from simulator.session import SimSession, SimSessionConfig
from simulator.state import CampaignPhase, HourSnapshot
from simulator.transport_regime import TransportRegimeRefusal
from optimizer_fixtures import StubSmokeConstraintSet


PO2_DEFAULT = ("campaigns", "C0b_p_cleanup", "pO2_mbar_default")
C6_WINDOW_REFUSAL = "c6_joint_thermodynamic_liquid_fraction_window_empty"


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
            "metric": ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
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


class VaporOverflowExecutor:
    calls: int = 0

    def execute(self, config: object) -> object:
        self.calls += 1
        _pow10_pressure_or_raise(
            400.0,
            species="SiO",
            field="P_reference_Pa",
        )
        raise AssertionError("vapor overflow path should raise before return")


class _Stage:
    def __init__(self, collected_kg: dict[str, float] | None = None) -> None:
        self.collected_kg = collected_kg or {}


class _EvalLedger:
    def mol_by_account(self, account: str | None = None):
        balances = {
            "process.cleaned_melt": {"CaO": 1.0},
            "terminal.slag": {"CaO": 1.0},
        }
        if account is None:
            return {key: dict(value) for key, value in balances.items()}
        return dict(balances.get(account, {}))


class _TerminalSiO2Ledger:
    def mol_by_account(self, account: str | None = None):
        balances = {
            "process.cleaned_melt": {"SiO2": 1.0},
            "terminal.slag": {"SiO2": 1.0},
        }
        if account is None:
            return {key: dict(value) for key, value in balances.items()}
        return dict(balances.get(account, {}))


class _MidRunCaOTerminalSiO2Ledger:
    def mol_by_account(self, account: str | None = None):
        balances = {
            "process.cleaned_melt": {"CaO": 1.0},
            "terminal.slag": {"SiO2": 1.0},
        }
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
            energy_electrical_plus_evaporation_kWh=44.0,
            total_hours=1,
        )
        self.energy_electrical_plus_evaporation_cumulative_kWh = 44.0
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


def _snapshot(
    mass_balance_error_pct: float | None = 0.0,
    *,
    hour: int = 1,
    knudsen_regime_summary: dict[str, object] | None = None,
    mass_in_kg: float = 1000.0,
    mass_out_kg: float = 1000.0,
    mass_balance_error_category: str = "",
) -> HourSnapshot:
    if knudsen_regime_summary is None:
        knudsen_regime_summary = {
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
        }
    snapshot = HourSnapshot(
        hour=hour,
        campaign=CampaignPhase.C2A,
        temperature_C=1600.0,
        mass_in_kg=mass_in_kg,
        mass_out_kg=mass_out_kg,
        mass_balance_error_pct=mass_balance_error_pct,
        knudsen_regime_summary=knudsen_regime_summary,
    )
    if mass_balance_error_category:
        setattr(snapshot, "mass_balance_error_category", mass_balance_error_category)
    return snapshot


def _trace(
    *,
    mixed_stream: bool = False,
    snapshots: tuple[HourSnapshot, ...] | None = None,
) -> SimpleNamespace:
    condensed = ({(3, "SiO"): 20.0},)
    if mixed_stream:
        condensed = ({(3, "SiO"): 19.0, (3, "Fe"): 2.0},)
    return SimpleNamespace(
        snapshots=snapshots or (_snapshot(),),
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


def test_stale_melt_profile_refusal_returns_named_verdict() -> None:
    profile = _composition_eval_profile("residual_rump_at_stop")
    profile["constraints"]["gates"] = [
        *profile["constraints"]["gates"],
        "delivered_stream_purity",
    ]

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(execution=_execution()),
    )

    assert result.feasible is False
    assert result.failure_category is FailureCategory.STALE_PROFILE
    assert result.eval_spec is None
    assert result.cache_key is None
    assert "delivered_stream_purity" in result.notes[0]
    assert "residual_rump_at_stop" in result.notes[0]
    assert "FORCE_PROFILES=1" in result.notes[0]


def test_cleaned_melt_stage0_pool_soft_endpoint_sets_stop_partition() -> None:
    assert forbidden_gates_for_pool("cleaned_melt_at_stage0_exit") == (
        "delivered_stream_purity",
    )
    profile = _composition_eval_profile(
        "cleaned_melt_at_stage0_exit",
        target_id="stage0-clean-basalt-explore",
        oxides={
            "CaO": {
                "min": 0.0,
                "max": 50.0,
                "strict": False,
                "weight": 1.0,
            }
        },
    )
    window = profile["objectives"][0]["target"]["composition_window"]
    window["exploratory"] = True

    executor = FakeExecutor(execution=_execution())
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=executor,
    )

    assert result.feasible
    assert result.failure_category is None
    assert result.eval_spec is not None
    assert result.eval_spec.stop_at_stage0_exit is True
    assert executor.config.stop_at_stage0_exit is True
    score = result.objectives.as_mapping()[
        "composition_target:stage0-clean-basalt-explore"
    ]
    assert 0.0 < score < 1.0


def test_feasible_evaluate_trace_includes_knob_saturation_without_cache_key_change() -> None:
    patch = RecipePatch({("campaigns", "C0", "temp_range_C"): [20.0, 950.0]})
    result = evaluate(
        patch,
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(execution=_execution()),
    )

    assert result.feasible
    assert result.eval_spec is not None
    assert result.cache_key == cache_key(result.eval_spec)
    trace = result.run_reference.trace
    saturation = trace["knob_saturation"]
    assert saturation["schema_version"] == "knob-saturation-v1"
    assert saturation["red_flag"] is True
    assert saturation["pinned_count"] == 2
    assert {row["key"] for row in saturation["knobs"]} == {
        "campaigns.C0.temp_range_C[0]",
        "campaigns.C0.temp_range_C[1]",
    }


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
    refusal_diagnostic: dict[str, object] | None = None,
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
        refusal_diagnostic=refusal_diagnostic or {},
    )


def _valid_patch() -> RecipePatch:
    return RecipePatch({PO2_DEFAULT: 9.0})


def test_mars_deep_vacuum_pumping_gate_returns_typed_sc67_proposal() -> None:
    execution = _execution(backend_status="ok", backend_authoritative=True)
    deep = estimate_subambient_pump_cost(
        target_pressure_pa=1.0e-4,  # 1e-9 bar deep-vacuum point
        offgas_mol_per_s=0.02,
        duration_s=7.0 * 3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=1500.0,
    )
    execution.simulator.record.cost_rollup = {
        "pumping_diagnostic": {
            "schema_version": "pumping-cost-rollup-v1",
            "status": "pumping_feasibility_unresolved",
            "feedstock_id": "mars_global_mgs1",
            "body": "mars",
            "ambient_pressure_pa": MARS_DATUM_AMBIENT_PA,
            "pumping_electrical_kWh": deep.energy_kWh,
            "feasible": None,
            "rows": [
                {
                    "hour": 1,
                    "target_pressure_pa": 1.0e-4,
                    "offgas_mol_per_s": 0.02,
                    "duration_s": 7.0 * 3600.0,
                    "gas_temperature_K": 1500.0,
                    **deep.to_json(),
                }
            ],
        }
    }

    result = evaluate(
        RecipePatch({}),
        "mars_global_mgs1",
        "fast",
        profile={**PROFILE, "feedstock": "mars_global_mgs1"},
        executor=FakeExecutor(execution=execution),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.PROPOSED
    assert result.requires_re_evaluation
    assert result.failing_gates == ("pumping_feasibility",)
    margin = result.feasibility_margins["pumping_feasibility"]
    assert margin.status == "available"
    assert margin.status_reason == "pumping_feasibility_unresolved"
    evidence = margin.status_payload["pumping_cost_evidence"]
    assert evidence["rows"][0]["required_pump_speed_m3_s"] == pytest.approx(
        2_494_338.7854,
        rel=1.0e-9,
    )
    proposal = margin.status_payload["pressure_proposal"]
    assert proposal["status"] == "proposed"
    assert proposal["feasibility_scope"] == "pumping_only"
    assert proposal["requires_full_recipe_species_re_evaluation"] is True
    assert proposal["rows"][0]["requested_target_pressure_pa"] == pytest.approx(1.0e-4)
    assert proposal["rows"][0]["proposed_target_pressure_pa"] == pytest.approx(
        MARS_DATUM_AMBIENT_PA
    )
    assert proposal["rows"][0]["pumping_cost_evidence"]["regime"] == "vent-free"
    assert proposal["rows"][0]["pumping_cost_evidence"]["energy_kWh"] == 0.0


def test_sc67_pressure_proposal_reports_mixed_failing_rows_as_partial() -> None:
    proposal = evaluate_module._pumping_pressure_proposal(
        {
            "body": "mars",
            "ambient_pressure_pa": MARS_DATUM_AMBIENT_PA,
            "rows": [
                {
                    "hour": 1,
                    "feasible": None,
                    "target_pressure_pa": 1.0e-4,
                    "offgas_mol_per_s": 0.02,
                    "duration_s": 3600.0,
                    "gas_temperature_K": 300.0,
                },
                {
                    "hour": 2,
                    "feasible": False,
                    "target_pressure_pa": 1.0e-4,
                    "offgas_mol_per_s": 0.02,
                    "duration_s": None,
                    "gas_temperature_K": 300.0,
                },
            ],
        }
    )

    assert proposal["status"] == "partial"
    assert proposal["failing_row_count"] == 2
    assert proposal["proposed_row_count"] == 1
    assert proposal["unavailable_row_count"] == 1
    assert proposal["rows"][0]["hour"] == 1
    assert proposal["unavailable_rows"] == [
        {
            "row_index": 1,
            "hour": 2,
            "reason": "pumping row inputs are incomplete or invalid",
        }
    ]


def test_sc67_pressure_proposal_does_not_call_missing_boundary_an_empty_set() -> None:
    proposal = evaluate_module._pumping_pressure_proposal(
        {
            "status": "refused",
            "reason": "missing-ambient-pressure",
            "body": "mars",
            "ambient_pressure_pa": math.nan,
            "rows": [
                {
                    "hour": 1,
                    "feasible": False,
                    "target_pressure_pa": 1.0e-4,
                }
            ],
        }
    )

    assert proposal["status"] == "unavailable"
    assert proposal["failing_row_count"] == 1
    assert proposal["proposed_row_count"] == 0
    assert proposal["unavailable_row_count"] == 1


def test_missing_pumping_rollup_recomputes_and_refuses_subambient_flow() -> None:
    pumping_snapshot = SimpleNamespace(
        hour=1,
        temperature_C=300.0,
        overhead=SimpleNamespace(
            pressure_mbar=1.0e-6,
            headspace_temperature_K=300.0,
        ),
        O2_vented_mol_hr=72.0,
        mass_balance_error_pct=0.0,
        mass_in_kg=1000.0,
        mass_out_kg=1000.0,
    )
    execution = _execution(
        snapshots=(pumping_snapshot,),
        backend_status="ok",
        backend_authoritative=True,
    )
    assert not hasattr(execution.simulator.record, "cost_rollup")

    result = evaluate(
        RecipePatch({}),
        "mars_global_mgs1",
        "fast",
        profile={**PROFILE, "feedstock": "mars_global_mgs1"},
        executor=FakeExecutor(execution=execution),
    )

    assert not result.feasible
    margin = result.feasibility_margins["pumping_feasibility"]
    evidence = margin.status_payload["pumping_cost_evidence"]
    assert evidence["evidence_source"] == "evaluate-recomputed-missing-rollup"
    assert evidence["status"] == "pumping_feasibility_unresolved"
    assert evidence["rows"][0]["target_pressure_pa"] == pytest.approx(1.0e-4)


def test_missing_pumping_rollup_certified_energy_reaches_objective() -> None:
    pumping_snapshot = SimpleNamespace(
        hour=1,
        temperature_C=300.0,
        overhead=SimpleNamespace(
            pressure_mbar=5.0,
            headspace_temperature_K=300.0,
            validated_line_conductance_m3_s=1.0,
        ),
        O2_vented_mol_hr=36.0,
        mass_balance_error_pct=0.0,
        mass_in_kg=1000.0,
        mass_out_kg=1000.0,
    )
    execution = _execution(
        snapshots=(pumping_snapshot,),
        backend_status="ok",
        backend_authoritative=True,
    )
    expected = estimate_subambient_pump_cost(
        target_pressure_pa=500.0,
        offgas_mol_per_s=0.01,
        duration_s=3600.0,
        ambient_pressure_pa=MARS_DATUM_AMBIENT_PA,
        gas_temperature_K=300.0,
        validated_line_conductance_m3_s=1.0,
    )

    result = evaluate(
        RecipePatch({}),
        "mars_global_mgs1",
        "fast",
        profile={**PROFILE, "feedstock": "mars_global_mgs1"},
        executor=FakeExecutor(execution=execution),
    )

    assert result.feasible
    assert result.objectives is not None
    assert result.objectives.as_mapping()[
        ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC
    ] == pytest.approx(44.0 + expected.energy_kWh)
    assert not hasattr(execution.simulator.record, "cost_rollup")


def test_malformed_ok_pumping_diagnostic_fails_closed() -> None:
    malformed = (
        {
            "status": "ok",
            "pumping_electrical_kWh": 0.0,
            "feasible": True,
            "rows": [],
        },
        {
            "status": "no_rows",
            "pumping_electrical_kWh": 99.0,
            "feasible": True,
            "rows": [],
        },
        {
            "status": "ok",
            "pumping_electrical_kWh": 0.0,
            "feasible": True,
            "rows": [{"feasible": True}],
        },
    )
    for diagnostic in malformed:
        execution = _execution(backend_status="ok", backend_authoritative=True)
        execution.simulator.record.cost_rollup = {
            "pumping_diagnostic": {
                "feedstock_id": "mars_global_mgs1",
                "body": "mars",
                "ambient_pressure_pa": MARS_DATUM_AMBIENT_PA,
                **diagnostic,
            }
        }

        result = evaluate(
            RecipePatch({}),
            "mars_global_mgs1",
            "fast",
            profile={**PROFILE, "feedstock": "mars_global_mgs1"},
            executor=FakeExecutor(execution=execution),
        )

        assert not result.feasible
        margin = result.feasibility_margins["pumping_feasibility"]
        assert margin.status_payload["pressure_proposal"]["status"] == "unavailable"


def test_pumping_gate_rejects_diagnostic_from_different_feedstock_body() -> None:
    execution = _execution(backend_status="ok", backend_authoritative=True)
    execution.simulator.record.cost_rollup = {
        "pumping_diagnostic": {
            "status": "no_rows",
            "feedstock_id": "lunar_mare_low_ti",
            "body": "moon",
            "ambient_pressure_pa": 1.3e-7,
            "pumping_electrical_kWh": 0.0,
            "feasible": True,
            "rows": [],
        }
    }

    result = evaluate(
        RecipePatch({}),
        "mars_global_mgs1",
        "fast",
        profile={**PROFILE, "feedstock": "mars_global_mgs1"},
        executor=FakeExecutor(execution=execution),
    )

    assert not result.feasible
    evidence = result.feasibility_margins["pumping_feasibility"].status_payload[
        "pumping_cost_evidence"
    ]
    assert evidence["reason"] == "pumping-diagnostic-environment-mismatch"
    mismatch = evidence["identity_mismatch"]
    assert mismatch["expected_feedstock_id"] == "mars_global_mgs1"
    assert mismatch["observed_feedstock_id"] == "lunar_mare_low_ti"


def test_pumping_gate_marks_unavailable_pressure_set_without_fabricated_proposal() -> None:
    execution = _execution(backend_status="ok", backend_authoritative=True)
    execution.simulator.record.cost_rollup = {
        "pumping_diagnostic": {
            "schema_version": "pumping-cost-rollup-v1",
            "status": "refused",
            "reason": "missing-ambient-pressure",
            "feedstock_id": "lunar_mare_low_ti",
            "body": "",
            "ambient_pressure_pa": math.nan,
            "pumping_electrical_kWh": 0.0,
            "feasible": False,
            "rows": [],
        }
    }

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(execution=execution),
    )

    assert not result.feasible
    margin = result.feasibility_margins["pumping_feasibility"]
    proposal = margin.status_payload["pressure_proposal"]
    assert proposal["status"] == "unavailable"
    assert proposal["reason"] == "pumping rows unavailable"
    assert proposal["proposed_row_count"] == 0
    assert proposal["unavailable_rows"] == []


def _real_backend_profile() -> dict:
    return {
        **PROFILE,
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }


def _available_real_backend_execution(**kwargs: object) -> SimpleNamespace:
    return _execution(
        backend_status="ok",
        backend_authoritative=True,
        **kwargs,
    )


def _clamped_kernel_equilibrium():
    return diagnostics_to_equilibrium(
        LiquidusDiagnostics(
            phases_present=("liq",),
            phase_masses_kg={"liq": 1.0},
            liquid_fraction=1.0,
            fO2_log=-9.0,
            backend_status="ok",
            backend_diagnostics={
                "operating_point_clamped": True,
                "operating_point_transport": "subprocess",
                "temperature_clamped": True,
                "pressure_clamped": True,
                "requested_temperature_C": 650.0,
                "requested_pressure_bar": 1.0e-6,
                "solved_temperature_C": 800.0,
                "solved_pressure_bar": 1.0,
                "authoritative_for_requested_conditions": False,
                "authoritative_for_solved_conditions": True,
            },
        ),
        {
            "temperature_C": 650.0,
            "pressure_bar": 1.0e-6,
            "fO2_log": -9.0,
        },
    )


def _non_finite_payload_fixture(*, non_finite: bool):
    executor = FakeExecutor(
        exc=PT0NonFinitePayload(
            "non-finite value in PT-0 payload at $.SCSS_ppm: inf"
        )
        if non_finite
        else None,
        execution=None if non_finite else _available_real_backend_execution(),
    )
    return evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=_real_backend_profile(),
        executor=executor,
    )


def _assert_synthetic_not_run_reference(run_reference: object | None) -> None:
    assert run_reference is not None
    assert getattr(run_reference, "backend_status") == "not_run"
    assert getattr(run_reference, "backend_authoritative") is False
    trace = getattr(run_reference, "trace")
    assert isinstance(trace, dict)
    assert trace["backend_status"] == "not_run"
    assert trace["backend_authoritative"] is False
    assert trace["execution_status"] == "not_run"
    assert trace["runtime_status"] == "not_run"
    assert trace["backend_real_active"] is False
    assert trace["degradation_reason"] == "not_run"


def _inventory_overdraw_fixture(*, overdraw_kg: float | None):
    exc = None
    if overdraw_kg is not None:
        exc = ProposalRejected(
            "insufficient available 'FeO' in normal account "
            "'process.cleaned_melt': balance would be "
            f"-{overdraw_kg:.12g} kg"
        )
    executor = FakeExecutor(
        exc=exc,
        execution=None if exc is not None else _available_real_backend_execution(),
    )
    return evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=_real_backend_profile(),
        executor=executor,
    )


def _backend_availability_fixture(*, available: bool):
    executor = FakeExecutor(
        execution=_available_real_backend_execution() if available else None,
        exc=None if available else BackendUnavailableError("missing binary"),
    )
    return evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=_real_backend_profile(),
        executor=executor,
    )


def _mass_balance_gate_profile() -> dict:
    return {
        **PROFILE,
        "constraints": {"gates": ["furnace_temperature"]},
    }


@dataclass
class ProductionMassBalanceReplayExecutor:
    mass_balance_error_pct: float | None = 0.0
    mass_in_kg: float | None = None
    mass_out_kg: float | None = None
    mass_balance_error_category: str = ""
    calls: int = 0
    execution: object | None = None

    def execute(self, config: object) -> object:
        self.calls += 1
        execution = RunExecutor().execute(config)
        snapshots = tuple(getattr(execution, "snapshots", ()))
        assert snapshots
        overrides = {"mass_balance_error_pct": self.mass_balance_error_pct}
        if self.mass_in_kg is not None:
            overrides["mass_in_kg"] = self.mass_in_kg
        if self.mass_out_kg is not None:
            overrides["mass_out_kg"] = self.mass_out_kg
        first = replace(snapshots[0], **overrides)
        if self.mass_balance_error_category:
            setattr(
                first,
                "mass_balance_error_category",
                self.mass_balance_error_category,
            )
        patched_snapshots = (first, *snapshots[1:])
        self.execution = replace(execution, snapshots=patched_snapshots)
        return self.execution


@dataclass
class ProductionMassBalanceRealPathExecutor:
    mass_balance_error_pct: float | None = None
    mass_in_kg: float = 0.0
    mass_out_kg: float = 1.0
    mass_balance_error_category: str = "zero_input_basis_breach"
    calls: int = 0
    execution: object | None = None

    def execute(self, config: object) -> object:
        self.calls += 1
        session = SimSession()
        session.start(config)
        sim = session.simulator
        original_step = sim.step

        def marked_step() -> HourSnapshot:
            snapshot = original_step()
            marked = replace(
                snapshot,
                mass_balance_error_pct=self.mass_balance_error_pct,
                mass_in_kg=self.mass_in_kg,
                mass_out_kg=self.mass_out_kg,
            )
            if self.mass_balance_error_category:
                setattr(
                    marked,
                    "mass_balance_error_category",
                    self.mass_balance_error_category,
                )
            sim.record.snapshots[-1] = marked
            return marked

        sim.step = marked_step
        self.execution = RunExecutor().execute_session(
            session,
            hours=int(getattr(config, "hours")),
        )
        return self.execution


def _knudsen_gate_profile() -> dict:
    return {
        **PROFILE,
        "constraints": {"gates": ["knudsen_viscous"]},
        "run": {
            **PROFILE["run"],
            "campaign": "C2A_continuous",
            "hours": 24,
        },
        "fidelities": {
            "fast": {
                "backend_name": "stub",
                "hours": 24,
            }
        },
        "seed_recipes": [
            {
                "id": "pc-extract-na-shape",
                "source_campaign": "C2A_continuous",
                "patch": {
                    "campaigns": {
                        "C2A_continuous": {
                            "p_total_mbar": [5, 15],
                            "p_total_mbar_default": 10,
                            "temp_range_C": [1050, 1600],
                        }
                    }
                },
            }
        ],
    }


def _knudsen_no_flow_profile() -> dict:
    return {
        **PROFILE,
        "constraints": {"gates": ["knudsen_viscous"]},
    }


def test_profile_campaign_setting_respects_plural_source_campaigns() -> None:
    profile = _knudsen_gate_profile()
    seed = dict(profile["seed_recipes"][0])
    seed.pop("source_campaign")
    seed["source_campaigns"] = ["C2A_continuous"]
    profile["seed_recipes"] = [seed]

    assert (
        evaluate_module._profile_campaign_setting(
            profile,
            "C2A_continuous",
            "p_total_mbar_default",
        )
        == 10
    )
    assert (
        evaluate_module._profile_campaign_setting(
            profile,
            "C4",
            "p_total_mbar_default",
        )
        is None
    )


def _hand_knudsen_number(
    pressure_mbar: float,
    gas_temperature_C: float,
) -> float:
    boltzmann_j_k = 1.380649e-23
    n2_collision_diameter_m = 3.798e-10  # BUG-013: grounded BSL Table E.1 sigma
    gas_temperature_k = gas_temperature_C + 273.15
    pressure_pa = pressure_mbar * 100.0
    pipe_diameter_m = 0.12
    mean_free_path_m = (
        boltzmann_j_k
        * gas_temperature_k
        / (
            math.sqrt(2.0)
            * math.pi
            * n2_collision_diameter_m**2
            * pressure_pa
        )
    )
    return mean_free_path_m / pipe_diameter_m


def _hand_knudsen_number_10_mbar_1600c() -> float:
    return _hand_knudsen_number(10.0, 1600.0)


def _hand_knudsen_number_5_mbar_1600c() -> float:
    return _hand_knudsen_number(5.0, 1600.0)


def _flow_present_missing_knudsen_snapshot() -> HourSnapshot:
    snapshot = _snapshot(knudsen_regime_summary={})
    snapshot.evap_flux.species_kg_hr = {"Na": 1.0}
    snapshot.evap_flux.total_kg_hr = 1.0
    snapshot.condensed_by_stage_species_delta = {(4, "Na"): 1.0}
    return snapshot


def _zero_overhead_flow_snapshot(*, marked: bool = True) -> HourSnapshot:
    summary = (
        {
            "status": "not_applicable",
            "reason": "not-applicable-zero-overhead-flow",
            "provenance": "not-applicable-zero-overhead-flow",
        }
        if marked
        else {}
    )
    snapshot = _snapshot(knudsen_regime_summary=summary)
    snapshot.evap_flux.species_kg_hr = {}
    snapshot.evap_flux.total_kg_hr = 0.0
    snapshot.condensed_by_stage_species_delta = {}
    snapshot.wall_deposit_by_segment_species_delta = {}
    for name in (
        "O2_vented_kg_hr",
        "O2_vented_mol_hr",
        "melt_offgas_O2_mol_hr",
        "mre_anode_O2_mol_hr",
    ):
        setattr(snapshot, name, 0.0)
    return snapshot


def test_batch_global_knudsen_summary_feeds_finite_physics_gate() -> None:
    expected_kn = _hand_knudsen_number_10_mbar_1600c()
    assert expected_kn == pytest.approx(3.3627904232901555e-4)
    trace = _trace(
        snapshots=(
            _snapshot(
                knudsen_regime_summary={
                    "status": "ok",
                    "knudsen_regime": "viscous",
                    "knudsen_number": expected_kn,
                    "regime_factor": expected_kn / (expected_kn + 0.01),
                    "warnings": (),
                }
            ),
        )
    )

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=_knudsen_gate_profile(),
        executor=FakeExecutor(
            _execution(
                trace=trace,
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    margin = result.feasibility_margins["knudsen_viscous"]
    assert margin.feasible
    assert margin.observed == pytest.approx(expected_kn)
    assert 2.0e-4 < margin.observed < 8.0e-4
    assert margin.detail.startswith("fallback:global-summary")
    assert "global_pipe" in margin.detail


def test_measured_segment_knudsen_margin_keeps_measured_detail() -> None:
    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=_knudsen_gate_profile(),
        executor=FakeExecutor(
            _execution(
                trace=_trace(),
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    margin = result.feasibility_margins["knudsen_viscous"]
    assert margin.feasible
    assert margin.observed == pytest.approx(0.001)
    assert not margin.detail.startswith("fallback:")
    assert "hot_wall" in margin.detail


def test_repro_profile_flow_present_empty_snapshot_knudsen_fails_closed() -> None:
    profile = _knudsen_gate_profile()
    del profile["seed_recipes"][0]["patch"]["campaigns"]["C2A_continuous"][
        "p_total_mbar_default"
    ]
    trace = _trace(snapshots=(_flow_present_missing_knudsen_snapshot(),))

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(
            _execution(
                trace=trace,
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    margin = result.feasibility_margins["knudsen_viscous"]
    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert not margin.feasible
    assert margin.margin == -math.inf
    assert margin.observed == math.inf
    assert margin.detail.startswith("population-bug-missing-overhead-flow-state:")
    assert "flow present but knudsen_regime_summary absent" in margin.detail
    assert "fallback:eval-inputs" not in margin.detail


def test_zero_overhead_flow_knudsen_snapshot_is_not_applicable_pass(
    tmp_path,
) -> None:
    trace = _trace(snapshots=(_zero_overhead_flow_snapshot(marked=True),))

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=_knudsen_no_flow_profile(),
        executor=FakeExecutor(
            _execution(
                trace=trace,
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    assert result.feasible
    assert result.failure_category is None
    margin = result.feasibility_margins["knudsen_viscous"]
    assert margin.feasible
    assert margin.margin == pytest.approx(0.01)
    assert margin.observed == pytest.approx(0.0)
    assert margin.detail.startswith("not-applicable-zero-overhead-flow:")
    assert "zero_overhead_flow" in margin.detail

    assert result.eval_spec is not None
    store = ResultStore(
        tmp_path / "knudsen-results.sqlite",
        current_code_version=result.eval_spec.code_version,
        current_data_digests=result.eval_spec.data_digests,
    )
    assert result.run_reference is not None
    closure = {"status": "closed", "mass_balance_error_pct": 0.0}
    closed_product_summary = dict(result.run_reference.product_summary)
    product_yield_table = dict(closed_product_summary.get("product_yield_table") or {})
    product_yield_table["mass_closure"] = closure
    closed_product_summary["mass_closure"] = closure
    closed_product_summary["product_yield_table"] = product_yield_table
    storable = replace(
        result,
        run_reference=replace(
            result.run_reference,
            product_summary=closed_product_summary,
        ),
    )
    store.store(result.eval_spec, storable, created_at="2026-06-11T00:00:00Z")
    loaded = store.fetch(result.cache_key)
    assert loaded is not None
    loaded_margin = loaded.feasibility_margins["knudsen_viscous"]
    assert loaded_margin.detail == margin.detail
    assert loaded_margin.observed == pytest.approx(0.0)


def test_zero_overhead_flow_requires_marker_not_dataclass_defaults() -> None:
    trace = _trace(snapshots=(_zero_overhead_flow_snapshot(marked=False),))

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=_knudsen_no_flow_profile(),
        executor=FakeExecutor(
            _execution(
                trace=trace,
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    margin = result.feasibility_margins["knudsen_viscous"]
    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert not margin.feasible
    assert margin.detail.startswith("population-bug-missing-overhead-flow-state:")
    assert "not-applicable-zero-overhead-flow" not in margin.detail


def test_extraction_not_attempted_round_trips_through_result_store(tmp_path) -> None:
    snapshot = replace(_snapshot(), temperature_C=600.0)
    setattr(snapshot, "solidus_T_C", 950.0)
    trace = _trace(snapshots=(snapshot,))
    trace.product_ledger_kg = {}
    trace.terminal_rump_by_species_kg = {"SiO2": 1000.0}
    trace.condensed_by_stage_species_delta = ({},)
    profile = {
        **PROFILE,
        "constraints": {
            "gates": [
                "delivered_stream_purity",
                "extraction_completeness",
            ],
        },
    }

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(
            _execution(
                trace=trace,
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    margins = result.feasibility_margins
    for gate in ("delivered_stream_purity", "extraction_completeness"):
        margin = margins[gate]
        assert margin.status == "not-attempted"
        assert margin.output_status == "not_attempted"
        assert margin.status_reason == "not-attempted-no-volatilization"
        assert margin.status_payload["predicate"] == "melt_never_reached_solidus"

    assert result.eval_spec is not None
    assert result.run_reference is not None
    closure = {"status": "closed", "mass_balance_error_pct": 0.0}
    closed_product_summary = dict(result.run_reference.product_summary)
    closed_product_summary["mass_closure"] = closure
    storable = replace(
        result,
        run_reference=replace(
            result.run_reference,
            product_summary=closed_product_summary,
        ),
    )
    store = ResultStore(
        tmp_path / "not-attempted-results.sqlite",
        current_code_version=result.eval_spec.code_version,
        current_data_digests=result.eval_spec.data_digests,
    )
    store.store(result.eval_spec, storable, created_at="2026-06-11T00:00:00Z")
    loaded = store.fetch(result.cache_key)

    assert loaded is not None
    for gate in ("delivered_stream_purity", "extraction_completeness"):
        margin = loaded.feasibility_margins[gate]
        assert margin.status == "not-attempted"
        assert margin.output_status == "not_attempted"
        assert margin.status_reason == "not-attempted-no-volatilization"
        assert margin.status_payload["predicate"] == "melt_never_reached_solidus"


def test_below_solidus_with_routed_flow_is_honest_infeasible() -> None:
    snapshot = replace(_snapshot(), temperature_C=100.0)
    setattr(snapshot, "solidus_T_C", 950.0)
    snapshot.evap_flux.species_kg_hr = {"SiO": 0.9, "Fe": 0.1}
    snapshot.evap_flux.total_kg_hr = 1.0
    snapshot.condensed_by_stage_species_delta = {(3, "SiO"): 0.9, (3, "Fe"): 0.1}
    trace = _trace(mixed_stream=True, snapshots=(snapshot,))
    trace.product_ledger_kg = {}
    trace.terminal_rump_by_species_kg = {"SiO2": 1000.0}
    trace.condensed_by_stage_species_delta = ({(3, "SiO"): 0.9, (3, "Fe"): 0.1},)
    profile = {
        **PROFILE,
        "constraints": {
            "gates": [
                "delivered_stream_purity",
                "extraction_completeness",
            ],
        },
    }

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(
            _execution(
                trace=trace,
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    for gate in ("delivered_stream_purity", "extraction_completeness"):
        margin = result.feasibility_margins[gate]
        assert not margin.feasible
        assert margin.status != "not-attempted"
        assert margin.output_status != "not_attempted"
        assert margin.status_reason != "not-attempted-no-volatilization"
        assert "not-attempted-no-volatilization" not in margin.detail


def test_flow_present_missing_knudsen_summary_fails_closed() -> None:
    trace = _trace(snapshots=(_flow_present_missing_knudsen_snapshot(),))

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=_knudsen_no_flow_profile(),
        executor=FakeExecutor(
            _execution(
                trace=trace,
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    margin = result.feasibility_margins["knudsen_viscous"]
    assert not margin.feasible
    assert margin.margin == -math.inf
    assert margin.observed == math.inf
    assert margin.detail.startswith("population-bug-missing-overhead-flow-state:")
    assert "missing overhead flow state" in margin.detail
    assert "not-applicable-zero-overhead-flow" not in margin.detail


@pytest.mark.parametrize("flow_value", [math.nan, math.inf])
def test_nonfinite_flow_scalar_missing_knudsen_summary_fails_closed(
    flow_value: float,
) -> None:
    snapshot = _snapshot(knudsen_regime_summary={})
    setattr(snapshot, "O2_vented_mol_hr", flow_value)
    trace = _trace(snapshots=(snapshot,))

    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=_knudsen_no_flow_profile(),
        executor=FakeExecutor(
            _execution(
                trace=trace,
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    margin = result.feasibility_margins["knudsen_viscous"]
    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert not margin.feasible
    assert margin.detail.startswith("population-bug-missing-overhead-flow-state:")
    assert "fallback:eval-inputs" not in margin.detail
    assert "not-applicable-zero-overhead-flow" not in margin.detail


def test_zero_input_extraction_completeness_is_named_and_serializable(tmp_path) -> None:
    green_profile = {
        **PROFILE,
        "constraints": {
            "gates": ["extraction_completeness"],
            "target_species": ["CrO2"],
        },
    }
    green = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=green_profile,
        executor=RunExecutor(),
    )
    green_margin = green.feasibility_margins["extraction_completeness"]
    green_target = green.run_reference.product_summary["extraction_completeness"][
        "targets"
    ]["CrO2"]
    assert green.run_reference.trace is not None
    assert green_target["status"] == "reported"
    assert "denominator_target_equiv_mol=" in green_margin.detail
    assert "not-applicable" not in green_margin.detail

    poison_profile = {
        **green_profile,
        "feedstock": "lunar_highlands_lhs1",
    }
    result = evaluate(
        RecipePatch({}),
        "lunar_highlands_lhs1",
        "fast",
        profile=poison_profile,
        executor=RunExecutor(),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    margin = result.feasibility_margins["extraction_completeness"]
    assert not margin.feasible
    assert margin.margin == -math.inf
    assert margin.observed == math.inf
    assert margin.detail == "not-applicable: zero input basis for CrO2"
    assert result.failing_gates == ("extraction_completeness",)
    target = result.run_reference.product_summary["extraction_completeness"][
        "targets"
    ]["CrO2"]
    assert target["status"] == "insufficient-evidence"
    assert target["reason"] == "not-applicable: zero input basis for CrO2"

    assert result.eval_spec is not None
    store = ResultStore(
        tmp_path / "extraction-results.sqlite",
        current_code_version=result.eval_spec.code_version,
        current_data_digests=result.eval_spec.data_digests,
    )
    store.store(result.eval_spec, result, created_at="2026-06-11T00:00:00Z")


def test_nonzero_extraction_completeness_margin_is_hand_pinned() -> None:
    constraints = PhysicsConstraintSet(active_gates=("extraction_completeness",))
    trace = _trace()
    trace.terminal_rump_by_species_kg = {"SiO2": 1.0}
    result = evaluate(
        RecipePatch({}),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        constraints=constraints,
        executor=FakeExecutor(_execution(trace=trace)),
    )

    assert result.feasible
    margin = result.feasibility_margins["extraction_completeness"]
    assert margin.feasible
    assert margin.observed == pytest.approx(0.9923358418656291)
    assert margin.margin == pytest.approx(0.04233584186562911)
    assert margin.detail == (
        "SiO: product_target_equiv_mol=2154.98, "
        "residual_target_equiv_mol=16.6436, "
        "denominator_target_equiv_mol=2171.62"
    )


def test_stub_smoke_constraint_set_skips_missing_knudsen_gate_boundary() -> None:
    trace = _trace(snapshots=(_snapshot(knudsen_regime_summary={}),))

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        constraints=StubSmokeConstraintSet(),
        executor=FakeExecutor(_execution(trace=trace)),
    )

    assert result.feasible
    assert result.failure_category is None
    assert tuple(result.feasibility_margins) == ("stub_smoke",)


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
    gates = [
        gate
        for gate in PROFILE["constraints"]["gates"]
        if gate not in forbidden_gates_for_pool(pool)
    ]
    if not gates:
        gates = ["furnace_temperature"]
    profile = {
        **PROFILE,
        "profile_id": f"{target_id}-profile",
        "constraints": {"gates": gates},
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


def _best_tap_composition_profile(
    *,
    tap_hour: int,
    warm_start: bool,
    target_id: str = "pc-warmstart-tap-test",
) -> dict:
    profile = _composition_eval_profile(
        "terminal_rump_earned",
        target_id=target_id,
        oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
    )
    profile["run"] = {
        "campaign": "C2A_continuous",
        "hours": 24,
        "mass_kg": 1000.0,
        "backend_name": "stub",
    }
    profile["fidelities"] = {"fast": {"backend_name": "stub"}}
    target = profile["objectives"][0]["target"]
    target["maturity"] = {
        "mode": "campaign_hours",
        "campaign": "C2A_continuous",
        "hours": 24,
        "best_tap": {
            "enabled": True,
            "tap_grid": [tap_hour],
            "tap_stability_hours": 2,
        },
    }
    if warm_start:
        profile["seed_recipes"] = [
            {
                "id": "thermal-window-seed",
                "source_campaign": "C2A_continuous",
                "patch": {
                    "campaigns": {
                        "C2A_continuous": {
                            "p_total_mbar_default": 10.0,
                            "temp_range_C": [1050.0, 1600.0],
                            "duration_h": 24,
                        }
                    }
                },
            }
        ]
    return profile


def _lab_schedule_best_tap_window() -> dict:
    return {
        "id": "composition_target_lab_schedule",
        "duration_h": 26.0,
        "interpolation": "piecewise_linear",
        "interpolation_source_class": "assumption_with_sensitivity_marker",
        "interpolation_citation_id": "test",
        "interpolation_extraction_note": "test-declared piecewise schedule",
        "furnace_ceiling_C": 1700.0,
        "experiment_windows": {
            "heating": {"start_h": 0.0, "end_h": 26.0},
            "measured": {"start_h": 2.0, "end_h": 24.0},
            "cooldown": {
                "duration_h": 2.0,
                "deposit_sampling": "cooldown_or_post_run",
            },
        },
        "melt_temperature_C": [
            {"t_h": 0.0, "value": 25.0, "unit": "C"},
            {"t_h": 2.0, "value": 1050.0, "unit": "C"},
            {"t_h": 26.0, "value": 1600.0, "unit": "C"},
        ],
        "chamber_pressure_mbar": [
            {"t_h": 0.0, "value": 10.0, "unit": "mbar"},
            {"t_h": 26.0, "value": 10.0, "unit": "mbar"},
        ],
        "window_semantics": {
            "preheat_h": 2.0,
            "measured_window_start_h": 2.0,
            "measured_window_end_h": 24.0,
            "cooldown_h": 2.0,
            "deposit_sample_basis": "after_cooldown",
        },
        "gas_boundary": {
            "background_gas": {
                "species": "Ar",
                "mole_fraction": 1.0,
                "source_class": "literature_sidecar",
                "source_ref": "test-methods",
            },
            "imposed_flow": {
                "value": 0.3,
                "unit": "NL_min",
                "source_class": "literature_sidecar",
                "source_ref": "test-methods",
            },
            "pressure_control": {
                "mode": "flow_through_with_pump",
                "source_class": "literature_sidecar",
                "source_ref": "test-methods",
            },
        },
    }


def _best_tap_execution(*hours: int) -> SimpleNamespace:
    snapshots = tuple(_best_tap_snapshot(hour) for hour in hours)
    return _execution(
        backend_status="ok",
        snapshots=snapshots,
        trace=_trace(snapshots=snapshots),
    )


def _best_tap_snapshot(hour: int) -> SimpleNamespace:
    snapshot = _snapshot(hour=hour)
    return SimpleNamespace(
        hour=snapshot.hour,
        campaign=snapshot.campaign,
        temperature_C=snapshot.temperature_C,
        mass_balance_error_pct=snapshot.mass_balance_error_pct,
        knudsen_regime_summary=snapshot.knudsen_regime_summary,
        inventory=SimpleNamespace(melt_oxide_kg={"CaO": 1.0}),
    )


def test_non_finite_payload_green_path_accepts_finite_authoritative_run() -> None:
    result = _non_finite_payload_fixture(non_finite=False)

    assert result.feasible
    assert result.failure_category is None
    assert result.failing_gates == ()
    assert result.objectives is not None
    assert all(math.isfinite(value) for value in result.objectives.as_mapping().values())
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "ok"


def test_non_finite_payload_poison_pair_fires_named_gate() -> None:
    result = _non_finite_payload_fixture(non_finite=True)

    assert result.feasible is False
    assert result.failure_category is FailureCategory.NON_FINITE_PAYLOAD
    assert result.failing_gates == ("non_finite_payload",)
    assert result.feasibility_margins["non_finite_payload"].feasible is False
    _assert_synthetic_not_run_reference(result.run_reference)


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
    _assert_synthetic_not_run_reference(result.run_reference)
    assert any("CALC_BUG" in note for note in result.notes)


def test_vapor_pressure_overflow_exception_is_bounded_infeasible() -> None:
    executor = VaporOverflowExecutor()

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=executor,
    )

    assert result.feasible is False
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert result.failing_gates == ("numerical_overflow",)
    assert result.run_reference is not None
    assert result.run_reference.reason == "numerical_overflow"
    assert result.run_reference.error_message.startswith(
        "VaporPressureNumericalOverflowError"
    )
    assert "numerical_overflow" in result.feasibility_margins
    assert result.feasibility_margins["numerical_overflow"].feasible is False
    assert math.isfinite(result.feasibility_margins["numerical_overflow"].margin)
    assert executor.calls == 1


def test_runner_wrapped_numerical_overflow_is_bounded_infeasible() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(
            exc=RunnerError(
                "VaporPressureNumericalOverflowError: "
                "vapor_pressure_numerical_overflow: species=SiO "
                "field=P_reference_Pa log_pressure=400.0"
            )
        ),
    )

    assert result.feasible is False
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert result.failing_gates == ("numerical_overflow",)
    assert result.run_reference is not None
    assert result.run_reference.reason == "numerical_overflow"
    assert "vapor_pressure_numerical_overflow" in result.run_reference.error_message


def test_failed_run_numerical_overflow_message_is_bounded_infeasible() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(
            _execution(
                status="failed",
                error_message=(
                    "VaporPressureNumericalOverflowError: "
                    "vapor_pressure_numerical_overflow: species=SiO "
                    "field=P_reference_Pa log_pressure=400.0"
                ),
                backend_status="ok",
                backend_authoritative=True,
            )
        ),
    )

    assert result.feasible is False
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert result.failing_gates == ("numerical_overflow",)
    assert result.run_reference is not None
    assert result.run_reference.reason == "numerical_overflow"
    assert result.run_reference.backend_status == "ok"
    assert result.run_reference.backend_authoritative is True


def test_failed_run_infra_result_too_large_is_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="result too large for cache slot"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(
                _execution(
                    status="failed",
                    error_message="backend failure: result too large for cache slot",
                    backend_status="ok",
                    backend_authoritative=True,
                )
            ),
        )


def test_executor_generic_overflow_is_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="result too large for cache slot"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(
                exc=OverflowError("backend failure: result too large for cache slot")
            ),
        )


def test_failed_run_vapor_overflow_substring_without_prefix_is_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="cache slot"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(
                _execution(
                    status="failed",
                    error_message=(
                        "backend failure: vapor_pressure_numerical_overflow "
                        "marker in cache slot"
                    ),
                    backend_status="ok",
                    backend_authoritative=True,
                )
            ),
        )


def test_objective_overflow_after_completed_run_is_engine_bug(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise OverflowError(34, "Result too large")

    monkeypatch.setattr(evaluate_module, "compute_objectives", explode)

    with pytest.raises(EngineBugAbort, match="OverflowError"):
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(_execution()),
        )


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
    assert result.run_reference.backend_status == "ok"
    assert result.run_reference.backend_authoritative is True


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
    _assert_synthetic_not_run_reference(result.run_reference)
    assert any("overdraw_kg=7.87e-05" in note for note in result.notes)


def test_proposal_rejected_runner_paths_are_invalid_recipe() -> None:
    cases = (
        (
            FakeExecutor(
                exc=RunnerError(
                    "ProposalRejected: insufficient available 'Cr2O3' in normal "
                    "account 'process.cleaned_melt': balance would be -0.125 kg"
                )
            ),
            "not_run",
            False,
        ),
        (
            FakeExecutor(
                _execution(
                    status="failed",
                    error_message=(
                        "ProposalRejected: insufficient available 'Al2O3' in normal "
                        "account 'process.cleaned_melt': balance would be -2.5 kg"
                    ),
                    backend_status="ok",
                    backend_authoritative=True,
                ),
            ),
            "ok",
            True,
        ),
    )
    for executor, expected_status, expected_authoritative in cases:
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
        assert result.run_reference.backend_status == expected_status
        assert result.run_reference.backend_authoritative is expected_authoritative
        if expected_status == "not_run":
            _assert_synthetic_not_run_reference(result.run_reference)


def test_inventory_overdraw_green_path_accepts_clean_ledger_execution() -> None:
    result = _inventory_overdraw_fixture(overdraw_kg=None)

    assert result.feasible
    assert result.failure_category is None
    assert result.failing_gates == ()
    assert result.objectives is not None
    assert result.run_reference is not None
    assert result.run_reference.product_summary["product_bins"]["ingots_metals"][
        "species_kg"
    ]["Fe"] == (
        pytest.approx(1.0)
    )


def test_inventory_overdraw_poison_pair_fires_named_gate() -> None:
    result = _inventory_overdraw_fixture(overdraw_kg=7.87e-05)

    assert result.feasible is False
    assert result.failure_category is FailureCategory.INVALID_RECIPE
    assert result.failing_gates == ("inventory_overdraw",)
    assert result.feasibility_margins["inventory_overdraw"].observed == pytest.approx(
        7.87e-05
    )


def test_mass_balance_green_path_scores_with_gate_engaged_at_abort_boundary(
    monkeypatch,
) -> None:
    executor = ProductionMassBalanceReplayExecutor(mass_balance_error_pct=5.0e-12)
    gate_calls: list[bool] = []
    original_gate = evaluate_module._abort_on_mass_balance_breach

    def spy_gate(*args, **kwargs):
        gate_calls.append(True)
        return original_gate(*args, **kwargs)

    monkeypatch.setattr(evaluate_module, "_abort_on_mass_balance_breach", spy_gate)

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_mass_balance_gate_profile(),
        executor=executor,
    )

    assert result.feasible
    assert result.failure_category is None
    assert result.failing_gates == ()
    assert executor.calls == 1
    assert type(executor.execution).__name__ == "RunExecution"
    assert executor.execution.snapshots[0].mass_balance_error_pct == pytest.approx(
        5.0e-12
    )
    assert result.run_reference is not None
    assert gate_calls == [True]


def test_mass_balance_poison_pair_fires_named_gate() -> None:
    executor = ProductionMassBalanceReplayExecutor(mass_balance_error_pct=5.1e-12)

    with pytest.raises(EngineBugAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=_mass_balance_gate_profile(),
            executor=executor,
        )

    assert raised.value.category is FailureCategory.ENGINE_BUG
    assert "mass balance breach" in str(raised.value)


def test_mass_balance_zero_input_nonzero_output_fires_named_gate() -> None:
    executor = ProductionMassBalanceRealPathExecutor()

    with pytest.raises(EvaluationAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=_mass_balance_gate_profile(),
            executor=executor,
        )

    assert raised.value.category is FailureCategory.ZERO_INPUT_BASIS_BREACH
    assert "zero_input_basis_breach" in str(raised.value)
    assert "mass_in_kg=0.0" in str(raised.value)
    assert "mass_out_kg=1.0" in str(raised.value)
    assert executor.calls == 1
    assert executor.execution is not None
    assert getattr(executor.execution, "status") == "ok"
    per_hour = getattr(executor.execution, "per_hour")[0]
    assert per_hour["mass_balance_pct"] is None
    assert per_hour["mass_balance_error_category"] == "zero_input_basis_breach"


def test_zero_mass_direct_run_executor_refuses_before_execution() -> None:
    bundle = load_config_bundle()
    with pytest.raises(
        RuntimeError,
        match="load_batch failed: batch mass_kg must be finite and > 0",
    ):
        RunExecutor().execute(
            SimSessionConfig(
                feedstock_id="lunar_mare_low_ti",
                feedstocks=bundle.feedstocks,
                setpoints=bundle.setpoints,
                vapor_pressures=bundle.vapor_pressures,
                campaign="C0",
                backend_name="stub",
                backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
                hours=1,
                mass_kg=0.0,
                force_builtin_vapor_pressure=_force_builtin_vapor_pressure,
            )
        )


def test_mass_balance_zero_input_zero_output_is_vacuously_closed_for_gate() -> None:
    executor = ProductionMassBalanceReplayExecutor(
        mass_balance_error_pct=0.0,
        mass_in_kg=0.0,
        mass_out_kg=0.0,
    )

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_mass_balance_gate_profile(),
        executor=executor,
    )

    assert result.feasible
    assert result.failure_category is None
    assert executor.execution is not None
    assert executor.execution.snapshots[0].mass_balance_error_pct == pytest.approx(0.0)
    assert not hasattr(executor.execution.snapshots[0], "mass_balance_error_category")


def test_zero_mass_eval_input_returns_named_ingress_refusal() -> None:
    profile = {
        **_mass_balance_gate_profile(),
        "run": {**PROFILE["run"], "mass_kg": 0.0},
    }
    executor = FakeExecutor(_execution())

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=executor,
    )

    assert result.feasible is False
    assert result.failure_category is FailureCategory.ZERO_INPUT_BASIS_BREACH
    assert result.failing_gates == ("zero_input_basis_breach",)
    assert result.eval_spec is None
    assert result.cache_key is None
    assert "zero_input_basis_breach" in result.notes[0]
    assert executor.calls == 0
    _assert_synthetic_not_run_reference(result.run_reference)


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


def test_pO2_enforcement_rows_surface_in_optimizer_result_artifact() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(
            _execution(
                backend_status="ok",
                per_hour=(
                    {
                        "hour": 1,
                        "campaign": "C2A",
                        "T_C": 625.0,
                        "pO2_enforcement": {
                            "hour": 1,
                            "setpoint_mbar": 3.0,
                            "achieved_mbar": 1.0,
                            "limited_by_total_pressure": True,
                            "status": "clipped_to_total_pressure",
                        },
                    },
                ),
            )
        ),
    )

    assert result.run_reference is not None
    trace = result.run_reference.trace
    assert trace["pO2_enforcement_by_hour"] == [
        {
            "hour": 1,
            "setpoint_mbar": 3.0,
            "achieved_mbar": 1.0,
            "limited_by_total_pressure": True,
            "status": "clipped_to_total_pressure",
        }
    ]


def test_objective_definitions_keep_profile_order_as_ordinal() -> None:
    definitions = objective_definitions(PROFILE)

    assert [(definition.metric, definition.sense, definition.ordinal) for definition in definitions] == [
        ("pure_silica_glass_kg", "maximize", 0),
        ("oxygen_kg", "maximize", 1),
        (ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC, "minimize", 2),
        ("duration_h", "minimize", 3),
    ]


def test_missing_objective_output_on_feasible_run_aborts_as_engine_bug() -> None:
    execution = _execution()
    delattr(
        execution.simulator,
        "energy_electrical_plus_evaporation_cumulative_kWh",
    )
    delattr(execution.simulator.record, "energy_electrical_plus_evaporation_kWh")

    with pytest.raises(
        EngineBugAbort,
        match="energy_electrical_plus_evaporation_kWh is missing",
    ) as raised:
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
    trace = _trace()
    trace.rump_terminal = {"status": "not_earned", "reason": "kernel_liquidus_disagree"}
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_composition_eval_profile(
            "terminal_rump_earned",
            target_id="pc-ceramic-test",
            oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        ),
        executor=FakeExecutor(_execution(trace=trace)),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert result.objectives is None
    assert result.failing_gates == ("rump_terminal",)
    assert "rump_terminal_unproven" in result.notes


def test_composition_target_terminal_rump_completed_run_scores_with_trace() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_composition_eval_profile(
            "terminal_rump_earned",
            target_id="pc-ceramic-test",
            oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        ),
        executor=FakeExecutor(_execution(backend_status="ok")),
    )

    assert result.feasible
    assert result.objectives is not None
    assert result.objectives.as_mapping()["composition_target:pc-ceramic-test"] == (
        pytest.approx(1.0)
    )
    assert result.run_reference is not None
    payload = result.run_reference.trace["composition_target"]
    assert payload["terminal_rump_source"] == "completed_run"
    assert payload["certification_tier"] == "certified"
    assert payload["certified_envelope"]


def test_warm_start_terminal_best_tap_uses_extended_configured_hours() -> None:
    target_id = "pc-warm-terminal"
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_best_tap_composition_profile(
            tap_hour=26,
            warm_start=True,
            target_id=target_id,
        ),
        executor=FakeExecutor(_best_tap_execution(26)),
    )

    assert result.feasible
    assert result.eval_spec is not None
    assert result.eval_spec.hours == 26
    assert result.objectives is not None
    payload = result.objectives.evidence[f"composition_target:{target_id}"][
        "composition_target"
    ]
    instruction = payload["operator_instruction"]
    assert payload["tap_hour"] == 26
    assert payload["configured_hours"] == 26
    assert payload["tap_provenance"] == "completed_run"
    assert payload["truncated_recipe"]["provenance"] == "completed_run"
    assert instruction["tap_hour"] == 26
    assert instruction["configured_hours"] == 26
    assert instruction["provenance"] == "completed_run"


def test_terminal_rump_best_tap_does_not_score_mid_run_snapshot_as_terminal() -> None:
    target_id = "pc-terminal-rump-true-terminal"
    profile = _best_tap_composition_profile(
        tap_hour=12,
        warm_start=False,
        target_id=target_id,
    )
    window = profile["objectives"][0]["target"]["composition_window"]
    window["oxides"] = {"CaO": {"min": 90.0, "max": 100.0, "weight": 1.0}}
    execution = _best_tap_execution(12)
    execution.simulator.atom_ledger = _TerminalSiO2Ledger()

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(execution),
    )

    assert result.feasible
    assert result.objectives is not None
    assert result.objectives.as_mapping()[f"composition_target:{target_id}"] == (
        pytest.approx(0.0)
    )
    payload = result.objectives.evidence[f"composition_target:{target_id}"][
        "composition_target"
    ]
    assert payload["tap_hour"] == 12
    assert payload["tap_provenance"] == "tap_truncated"
    assert payload["terminal_rump_source"] == "tap_truncated"
    assert payload["terminal_rump_nonterminal_reason"] == (
        "terminal_rump_nonterminal_best_tap"
    )
    assert payload["resolved_composition"]["oxide_wt_pct"] == {}


def test_terminal_rump_best_tap_rejects_aligned_mid_run_cleaned_melt_as_terminal() -> None:
    target_id = "pc-terminal-rump-aligned-mid-run"
    profile = _best_tap_composition_profile(
        tap_hour=12,
        warm_start=False,
        target_id=target_id,
    )
    window = profile["objectives"][0]["target"]["composition_window"]
    window["oxides"] = {"CaO": {"min": 90.0, "max": 100.0, "weight": 1.0}}
    execution = _best_tap_execution(12)

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(execution),
    )

    assert result.feasible
    assert result.objectives is not None
    assert result.objectives.as_mapping()[f"composition_target:{target_id}"] == (
        pytest.approx(0.0)
    )
    payload = result.objectives.evidence[f"composition_target:{target_id}"][
        "composition_target"
    ]
    assert payload["tap_hour"] == 12
    assert payload["configured_hours"] == 24
    assert payload["tap_provenance"] == "tap_truncated"
    assert payload["terminal_rump_source"] == "tap_truncated"
    assert payload["terminal_rump_nonterminal_reason"] == (
        "terminal_rump_nonterminal_best_tap"
    )


def test_terminal_rump_completed_best_tap_uses_terminal_slag_not_cleaned_melt() -> None:
    target_id = "pc-terminal-rump-true-terminal-ledger"
    profile = _best_tap_composition_profile(
        tap_hour=24,
        warm_start=False,
        target_id=target_id,
    )
    window = profile["objectives"][0]["target"]["composition_window"]
    window["oxides"] = {"CaO": {"min": 90.0, "max": 100.0, "weight": 1.0}}
    execution = _best_tap_execution(24)
    execution.simulator.atom_ledger = _MidRunCaOTerminalSiO2Ledger()

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(execution),
    )

    assert result.feasible
    assert result.objectives is not None
    assert result.objectives.as_mapping()[f"composition_target:{target_id}"] == (
        pytest.approx(0.0)
    )
    payload = result.objectives.evidence[f"composition_target:{target_id}"][
        "composition_target"
    ]
    assert payload["tap_hour"] == 24
    assert payload["tap_provenance"] == "completed_run"
    assert payload["terminal_rump_source"] == "completed_run"
    assert payload["resolved_composition"]["oxide_wt_pct"]["CaO"] == pytest.approx(0.0)


def test_warm_start_mid_window_best_tap_is_absolute_truncated_with_preheat() -> None:
    target_id = "pc-warm-mid-window"
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_best_tap_composition_profile(
            tap_hour=24,
            warm_start=True,
            target_id=target_id,
        ),
        executor=FakeExecutor(_best_tap_execution(24)),
    )

    assert result.feasible
    assert result.eval_spec is not None
    assert result.eval_spec.hours == 26
    assert result.objectives is not None
    payload = result.objectives.evidence[f"composition_target:{target_id}"][
        "composition_target"
    ]
    instruction = payload["operator_instruction"]
    truncated = payload["truncated_recipe"]
    assert payload["tap_hour"] == 24
    assert payload["configured_hours"] == 26
    assert payload["tap_provenance"] == "tap_truncated"
    assert payload["thermal_window_preheat_hours"] == pytest.approx(2.0)
    assert instruction["tap_hour"] == 24
    assert instruction["configured_hours"] == 26
    assert instruction["provenance"] == "tap_truncated"
    assert instruction["thermal_window_preheat_hours"] == pytest.approx(2.0)
    assert truncated["provenance"] == "tap_truncated"
    assert truncated["operator_instruction"]["thermal_window_preheat_hours"] == (
        pytest.approx(2.0)
    )


def test_lab_schedule_best_tap_reports_window_semantics_and_sample_basis() -> None:
    target_id = "pc-lab-schedule-window"
    profile = _best_tap_composition_profile(
        tap_hour=24,
        warm_start=False,
        target_id=target_id,
    )
    profile["run"] = {
        **profile["run"],
        "lab_schedule": _lab_schedule_best_tap_window(),
    }

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(_best_tap_execution(24)),
    )

    assert result.feasible
    assert result.eval_spec is not None
    assert result.eval_spec.hours == 26
    assert result.objectives is not None
    payload = result.objectives.evidence[f"composition_target:{target_id}"][
        "composition_target"
    ]
    instruction = payload["operator_instruction"]
    truncated = payload["truncated_recipe"]
    window = payload["lab_schedule_window_semantics"]
    assert payload["thermal_window_preheat_hours"] == pytest.approx(2.0)
    assert window["measured_window_start_h"] == pytest.approx(2.0)
    assert window["measured_window_end_h"] == pytest.approx(24.0)
    assert window["cooldown_h"] == pytest.approx(2.0)
    assert payload["deposit_sample_basis"] == "after_cooldown"
    assert instruction["lab_schedule_window_semantics"] == window
    assert instruction["deposit_sample_basis"] == "after_cooldown"
    assert truncated["lab_schedule_window_semantics"] == window
    assert truncated["operator_instruction"]["deposit_sample_basis"] == (
        "after_cooldown"
    )


def test_cold_best_tap_keeps_completed_run_without_preheat_metadata() -> None:
    target_id = "pc-cold-terminal"
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_best_tap_composition_profile(
            tap_hour=24,
            warm_start=False,
            target_id=target_id,
        ),
        executor=FakeExecutor(_best_tap_execution(24)),
    )

    assert result.feasible
    assert result.eval_spec is not None
    assert result.eval_spec.hours == 24
    assert result.objectives is not None
    payload = result.objectives.evidence[f"composition_target:{target_id}"][
        "composition_target"
    ]
    instruction = payload["operator_instruction"]
    assert payload["tap_hour"] == 24
    assert payload["configured_hours"] == 24
    assert payload["tap_provenance"] == "completed_run"
    assert instruction["provenance"] == "completed_run"
    assert "thermal_window_preheat_hours" not in payload
    assert "thermal_window_preheat_hours" not in instruction


def test_trace_only_out_of_domain_terminal_rump_cannot_score_completed_run() -> None:
    trace = _trace()
    trace.backend_status = "out_of_domain"

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_composition_eval_profile(
            "terminal_rump_earned",
            target_id="pc-ceramic-test",
            oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        ),
        executor=FakeExecutor(_execution(trace=trace)),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.INFEASIBLE_RECIPE
    assert result.objectives is None
    assert result.failing_gates == ("rump_terminal",)
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "out_of_domain"
    assert result.run_reference.trace["rump_terminal"]["status"] == "not_earned"
    assert "rump_terminal_unproven" in result.notes
    assert "completed_run" not in result.notes


def test_trace_only_out_of_domain_earned_rump_terminal_scores_earned_crash() -> None:
    trace = _trace()
    trace.backend_status = "out_of_domain"
    trace.backend_diagnostics = _crash_diagnostics(temperature_C=1100.0)
    execution = _execution(trace=trace, freeze_curve=_kernel_curve())

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=_composition_eval_profile(
            "terminal_rump_earned",
            target_id="pc-ceramic-test",
            oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
        ),
        executor=FakeExecutor(execution),
    )

    assert result.feasible
    assert result.failure_category is None
    assert result.objectives is not None
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "out_of_domain"
    assert result.run_reference.trace["rump_terminal"]["status"] == "earned"
    assert result.run_reference.trace["composition_target"]["terminal_rump_source"] == "earned_crash"


def test_composition_target_coating_gate_uses_runner_report_not_delta_heuristic() -> None:
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

    assert result.feasible
    assert result.failure_category is None
    assert result.objectives is not None
    assert result.failing_gates == ()
    coating = result.feasibility_margins["coating"]
    assert coating.observed == math.inf
    assert "runner wall-fouling" in coating.detail


def test_runner_wall_fouling_report_binds_optimizer_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "_wall_fouling_report",
        lambda *_args, **_kwargs: {
            "campaigns_to_resinter": 9.0,
            "resinter_threshold_kg": 4.5,
            "wall_deposit_kg_per_campaign": 0.5,
            "authoritative_for_resinter": True,
            "output_status": "authoritative",
            "status_reason": "",
            "sticking_alpha_authority": {"citation_status": "CITED"},
        },
    )
    profile = {
        **PROFILE,
        "constraints": {
            **PROFILE["constraints"],
            "gates": ["coating"],
            "coating_min_campaigns_to_resinter": 10.123,
        },
    }

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(_execution()),
    )

    assert not result.feasible
    assert result.failing_gates == ("coating",)
    coating = result.feasibility_margins["coating"]
    assert coating.observed == pytest.approx(9.0)
    assert coating.status_payload["resinter_threshold_kg"] == pytest.approx(4.5)
    assert coating.status_payload["wall_deposit_kg_per_campaign"] == pytest.approx(0.5)
    assert coating.status_payload["sticking_alpha_authority"] == {
        "citation_status": "CITED"
    }


def test_inactive_coating_gate_does_not_construct_runner_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.runner as runner_module

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("inactive coating gate constructed runner overlay")

    monkeypatch.setattr(runner_module, "_wall_fouling_report", fail_if_called)

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(_execution()),
    )

    assert result.feasible


def test_parametric_runner_fouling_report_has_coherent_non_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "_wall_fouling_report",
        lambda *_args, **_kwargs: {
            "campaigns_to_resinter": "resinter_threshold_kg / 0.5",
            "resinter_threshold_basis": "parameter required",
            "authoritative": True,
            "authoritative_for_resinter": True,
            "verdict_authoritative": True,
            "output_status": "authoritative",
            "status": "available",
            "status_reason": "",
            "nominal_verdict": "slow-fouling",
            "verdict": "slow-fouling",
        },
    )
    profile = {
        **PROFILE,
        "constraints": {"gates": ["coating"]},
    }

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=profile,
        executor=FakeExecutor(_execution()),
    )

    report = result.feasibility_margins["coating"].status_payload
    assert report["campaigns_to_resinter"] == "resinter_threshold_kg / 0.5"
    assert report["resinter_threshold_basis"] == "parameter required"
    assert report["authoritative"] is False
    assert report["authoritative_for_resinter"] is False
    assert report["verdict_authoritative"] is False
    assert report["output_status"] == "non-authoritative-threshold"
    assert report["status"] == "warning"
    assert report["verdict"] == "non-authoritative"
    assert report["nominal_verdict"] == "slow-fouling"


def test_knudsen_snapshot_replacement_preserves_coating_overlay() -> None:
    trace = _trace()
    trace.snapshots = (
        SimpleNamespace(
            knudsen_regime_summary={
                "status": "ok",
                "regime": "viscous",
                "knudsen_number": 0.001,
            }
        ),
    )
    report = {"campaigns_to_resinter_total": 7.0}
    overlay = evaluate_module._TraceAttributeOverlay(
        trace,
        {"wall_fouling_report": report},
    )

    prepared, missing = evaluate_module._trace_with_knudsen_observables(overlay)

    assert missing is None
    assert prepared is not overlay
    assert prepared.wall_fouling_report is report
    assert prepared.snapshots[0].knudsen_regime_summary["segments"]


def test_composition_target_constraint_augmentation_skips_stub_smoke_constraints() -> None:
    profile = _composition_eval_profile("residual_rump_at_stop")
    stub_smoke_constraints = object()

    assert (
        _composition_target_constraints(profile, stub_smoke_constraints)
        is stub_smoke_constraints
    )


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


def test_backend_unavailable_green_path_accepts_available_real_backend() -> None:
    result = _backend_availability_fixture(available=True)

    assert result.feasible
    assert result.failure_category is None
    assert result.eval_spec is not None
    assert result.eval_spec.backend_name == "alphamelts"
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "ok"


def test_metallic_real_backend_eval_fails_out_of_domain_before_executor() -> None:
    real_profile = {
        **PROFILE,
        "feedstock": "m_type_metallic_phase",
        "run": {**PROFILE["run"], "backend_name": "alphamelts"},
        "fidelities": {"high": {"backend_name": "alphamelts", "hours": 1}},
    }

    class RejectExecutor:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("executor must not run for non-silicate feedstock")

    result = evaluate(
        _valid_patch(),
        "m_type_metallic_phase",
        "high",
        profile=real_profile,
        executor=RejectExecutor(),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.OUT_OF_DOMAIN
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "out_of_domain"
    assert result.run_reference.backend_status_reason == "non_silicate_feedstock"
    assert "backend_status_reason=non_silicate_feedstock" in result.notes


def test_backend_unavailable_poison_pair_fires_named_gate() -> None:
    with pytest.raises(BackendUnavailableAbort) as raised:
        _backend_availability_fixture(available=False)

    assert raised.value.category is FailureCategory.BACKEND_UNAVAILABLE
    assert "missing binary" in str(raised.value)


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


def test_non_authoritative_backend_status_green_path_requires_authoritative_ok() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=_real_backend_profile(),
        executor=FakeExecutor(_available_real_backend_execution()),
    )

    assert result.feasible
    assert result.failure_category is None
    assert result.eval_spec is not None
    assert result.eval_spec.backend_name == "alphamelts"
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "ok"


def test_empty_crash_point_placeholder_does_not_mark_backend_out_of_domain() -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=_real_backend_profile(),
        executor=FakeExecutor(
            _available_real_backend_execution(
                backend_diagnostics={"backend_status": "ok", "crash_point": {}},
            )
        ),
    )

    assert result.feasible
    assert result.failure_category is None
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "ok"
    assert "melts_domain_out_of_domain" not in result.notes


def test_clamped_kernel_success_is_out_of_domain_at_requested_point() -> None:
    clamped = _clamped_kernel_equilibrium()
    clean_snapshot = _snapshot(MASS_BALANCE_ABORT_PCT)

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=_real_backend_profile(),
        executor=FakeExecutor(
            _execution(
                backend_status=clamped.status,
                backend_authoritative=True,
                backend_diagnostics=clamped.diagnostics,
                snapshots=(clean_snapshot,),
                trace=_trace(snapshots=(clean_snapshot,)),
            )
        ),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.OUT_OF_DOMAIN
    assert result.objectives is None
    assert result.failing_gates == ("backend_domain",)
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "out_of_domain"
    assert (
        "melts_domain_out_of_domain: backend_status=out_of_domain"
        in result.notes
    )



def test_out_of_domain_result_preserves_backend_status_reason() -> None:
    clean_snapshot = _snapshot(MASS_BALANCE_ABORT_PCT)

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=_real_backend_profile(),
        executor=FakeExecutor(
            _execution(
                backend_status="out_of_domain",
                backend_diagnostics={
                    "backend_status": "out_of_domain",
                    "backend_status_reason": "forbidden_species",
                },
                snapshots=(clean_snapshot,),
                trace=_trace(snapshots=(clean_snapshot,)),
            )
        ),
    )

    assert not result.feasible
    assert result.failure_category is FailureCategory.OUT_OF_DOMAIN
    assert result.run_reference is not None
    assert result.run_reference.backend_status == "out_of_domain"
    assert result.run_reference.backend_status_reason == "forbidden_species"
    assert result.run_reference.trace["backend_status_reason"] == "forbidden_species"
    assert result.feasibility_margins["backend_domain"].detail.endswith(
        ": forbidden_species"
    )
    assert any(
        note.endswith("backend_status_reason=forbidden_species")
        for note in result.notes
    )


def test_real_backend_out_of_domain_status_is_infeasible_result() -> None:
    clean_snapshot = _snapshot(MASS_BALANCE_ABORT_PCT)

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=_real_backend_profile(),
        executor=FakeExecutor(
            _execution(
                backend_status="out_of_domain",
                snapshots=(clean_snapshot,),
                trace=_trace(snapshots=(clean_snapshot,)),
            )
        ),
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
    assert "melts_domain_out_of_domain: backend_status=out_of_domain" in result.notes
    assert "rump_terminal: not_earned reason=missing_crash_point" in result.notes


def test_out_of_domain_terminal_rump_target_mass_balance_category_aborts() -> None:
    snapshot = _snapshot(
        mass_balance_error_pct=0.0,
        mass_in_kg=0.0,
        mass_out_kg=1.0,
        mass_balance_error_category=ZERO_INPUT_BASIS_BREACH,
    )
    profile = _composition_eval_profile(
        "terminal_rump_earned",
        target_id="pc-ceramic-test",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
    )

    with pytest.raises(EvaluationAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=profile,
            executor=FakeExecutor(
                _execution(
                    backend_status="out_of_domain",
                    snapshots=(snapshot,),
                    trace=_trace(snapshots=(snapshot,)),
                )
            ),
        )

    assert raised.value.category is FailureCategory.ZERO_INPUT_BASIS_BREACH
    assert ZERO_INPUT_BASIS_BREACH in str(raised.value)


def test_out_of_domain_plain_result_mass_balance_pct_aborts_as_engine_bug() -> None:
    snapshot = _snapshot(-(MASS_BALANCE_ABORT_PCT * 1.1))

    with pytest.raises(EngineBugAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "high",
            profile=_real_backend_profile(),
            executor=FakeExecutor(
                _execution(
                    backend_status="out_of_domain",
                    snapshots=(snapshot,),
                    trace=_trace(snapshots=(snapshot,)),
                )
            ),
        )

    assert raised.value.category is FailureCategory.ENGINE_BUG
    assert "mass balance breach" in str(raised.value)


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


def test_out_of_domain_earned_rump_terminal_composition_target_scores_success() -> None:
    class NoTerminalSlagLedger:
        def mol_by_account(self, account: str | None = None):
            balances = {"process.cleaned_melt": {"CaO": 1.0}}
            if account is None:
                return {key: dict(value) for key, value in balances.items()}
            return dict(balances.get(account, {}))

    trace = _trace()
    delattr(trace, "terminal_rump_by_species_kg")
    trace.condensed_by_stage_species_delta = ({(3, "SiO"): 20.0},)
    trace.wall_deposit_by_segment_species_kg = {("hot_wall", "SiO2"): 0.25}
    trace.wall_zone_by_segment = {"hot_wall": "Hot"}
    profile = _composition_eval_profile(
        "terminal_rump_earned",
        target_id="pc-terminal-rump-earned",
        oxides={"CaO": {"min": 0.0, "max": 100.0, "weight": 1.0}},
    )
    target = profile["objectives"][0]["target"]
    target["species_vector"] = {"Ca": "retain", "Si": "extract"}
    target["extraction"] = {
        "basis": "input_element_mol",
        "captured_pool": "captured_stage_3_silica",
        "completeness_min": {"Si": 1.0e-12},
    }
    target["score_weights"] = {"extraction": 0.5, "composition": 0.5}
    profile["run"] = {**PROFILE["run"], "backend_name": "alphamelts"}
    profile["fidelities"] = {"high": {"backend_name": "alphamelts", "hours": 1}}
    execution = _execution(
        trace=trace,
        backend_status="out_of_domain",
        backend_diagnostics=_crash_diagnostics(temperature_C=1100.0),
        freeze_curve=_kernel_curve(),
    )
    execution.simulator.atom_ledger = NoTerminalSlagLedger()
    execution.simulator.train.stages = (_Stage(), _Stage(), _Stage(), _Stage())

    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "high",
        profile=profile,
        executor=FakeExecutor(execution),
    )

    assert result.feasible
    assert result.failure_category is None
    assert result.objectives is not None
    assert result.objectives.as_mapping()["composition_target:pc-terminal-rump-earned"] == (
        pytest.approx(1.0)
    )
    assert "rump_terminal" in result.feasibility_margins
    rump_margin = result.feasibility_margins["rump_terminal"]
    assert rump_margin.feasible
    assert rump_margin.observed == pytest.approx(0.0)
    assert rump_margin.margin >= 0.0
    assert result.run_reference is not None
    result_trace = result.run_reference.trace
    assert result_trace["rump_terminal"]["status"] == "earned"
    assert result_trace["terminal_rump_by_species_kg"] == {"CaO": 2.0}
    assert result_trace["composition_target"]["terminal_rump_source"] == "earned_crash"
    saturation = result_trace["knob_saturation"]
    assert saturation["schema_version"] == "knob-saturation-v1"
    assert saturation["red_flag"] is False
    assert {row["key"] for row in saturation["knobs"]} == {
        "campaigns.C0b_p_cleanup.pO2_mbar_default"
    }
    assert result.run_reference.product_summary[
        "wall_deposit_kg_by_segment_species"
    ]["hot_wall"]["SiO2"] == pytest.approx(0.25)
    assert result.run_reference.product_summary[
        "wall_deposit_kg_by_zone_species"
    ]["Hot"]["SiO2"] == pytest.approx(0.25)


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
                backend_diagnostics=_crash_diagnostics(temperature_C=1300.0),
                freeze_curve=_kernel_curve(),
            )
        ),
    )
    assert result.eval_spec is not None
    assert result.feasible is False
    assert result.failure_category is FailureCategory.OUT_OF_DOMAIN
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version=result.eval_spec.code_version,
        current_data_digests=result.eval_spec.data_digests,
    )

    store.store(result.eval_spec, result, created_at="2026-06-09T00:00:00Z")

    loaded = store.lookup(result.eval_spec)
    assert loaded is not None
    assert loaded.feasible is False
    assert loaded.failure_category is FailureCategory.OUT_OF_DOMAIN
    assert loaded.run_reference is not None
    assert loaded.run_reference.backend_status == "out_of_domain"


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


def _typed_refusal_execution(reason: str) -> SimpleNamespace:
    return _execution(
        status="refused",
        error_message=reason,
        reason=reason,
        backend_status="ok",
        backend_authoritative=True,
        refusal_diagnostic={
            "status": "refused",
            "diagnostic": {"reason_refused": reason},
        },
    )


def _typed_refusal_cases() -> tuple[tuple[str, str, Exception | None, object | None], ...]:
    campaign_pressure = CampaignPressureSetpointRefusal(
        {"status": "refused", "detail": "empty pN2 operating band"}
    )
    return (
        (
            "mre_raw_margin",
            MRE_RAW_MARGIN_REFUSAL,
            None,
            _typed_refusal_execution(MRE_RAW_MARGIN_REFUSAL),
        ),
        (
            "mre_multi_oxide_partition",
            MRE_MULTI_OXIDE_PARTITION_REFUSAL,
            None,
            _typed_refusal_execution(MRE_MULTI_OXIDE_PARTITION_REFUSAL),
        ),
        (
            "c6_static_window",
            C6_WINDOW_REFUSAL,
            None,
            _typed_refusal_execution(C6_WINDOW_REFUSAL),
        ),
        (
            "campaign_pressure",
            campaign_pressure.reason,
            campaign_pressure,
            None,
        ),
        (
            "knudsen",
            KnudsenRegimeRefusal.reason,
            KnudsenRegimeRefusal(
                {"status": "refused", "reason_refused": KnudsenRegimeRefusal.reason}
            ),
            None,
        ),
        (
            "vapor_pressure_range",
            "metal_vapor_pressure_out_of_source_certified_range",
            VaporPressureRangeError(
                "metal_vapor_pressure_out_of_source_certified_range: species=Mg"
            ),
            None,
        ),
        (
            "liquidus_authority",
            "liquidus_authority_refused",
            CertifiedPointRefusedError(
                "certified-point refused for ungrounded effect CI.liquidus"
            ),
            None,
        ),
        (
            "transport_regime",
            "invalid_transport_regime_input",
            TransportRegimeRefusal("invalid_transport_regime_input", "bad Kn"),
            None,
        ),
    )


@pytest.mark.parametrize(
    ("family", "expected_reason", "exc", "execution"),
    _typed_refusal_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_typed_physics_refusals_are_infeasible_trials_with_reason(
    family: str,
    expected_reason: str,
    exc: Exception | None,
    execution: object | None,
) -> None:
    result = evaluate(
        _valid_patch(),
        "lunar_mare_low_ti",
        "fast",
        profile=PROFILE,
        executor=FakeExecutor(execution=execution, exc=exc),
    )

    assert family
    assert not result.feasible
    assert result.failure_category is FailureCategory.PHYSICS_REFUSED
    assert result.run_reference is not None
    assert result.run_reference.status == "refused"
    assert result.run_reference.reason == expected_reason
    assert expected_reason in result.notes
    assert result.failing_gates == ("physics_refusal",)
    assert result.feasibility_margins["physics_refusal"].detail == expected_reason


def test_untyped_executor_exception_still_aborts_as_engine_bug() -> None:
    with pytest.raises(EngineBugAbort, match="oxide ledger exploded") as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=FakeExecutor(exc=RuntimeError("oxide ledger exploded")),
        )

    assert raised.value.category is FailureCategory.ENGINE_BUG


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


def test_strict_vaporock_unavailable_eval_fails_closed_with_vaporock_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = {
        "profile_id": "strict-vapor-runtime-profile",
        "profile_schema_version": "profile-schema-v1",
        "feedstock": "lunar_mare_low_ti",
        "objectives": PROFILE["objectives"],
        "constraints": {"gates": ["delivered_stream_purity"]},
        "seed_recipes": [{"id": "seed", "source_campaign": "C0", "patch": {}}],
        "run": {
            "campaign": "C0",
            "hours": 1,
            "mass_kg": 1000.0,
            "backend_name": "stub",
            "allow_fallback_vapor": False,
            "force_builtin_vapor_pressure": False,
        },
        "fidelities": {"fast": {"backend_name": "stub", "hours": 1}},
    }
    executor = FakeExecutor(
        _execution(
            status="failed",
            error_message="VapoRock provider unavailable",
        )
    )

    monkeypatch.setattr(evaluate_module, "_vaporock_available", lambda: False)
    with pytest.raises(BackendUnavailableAbort) as raised:
        evaluate(
            _valid_patch(),
            "lunar_mare_low_ti",
            "fast",
            profile=profile,
            executor=executor,
        )

    kernel_config = executor.config.setpoints.get("chemistry_kernel", {})
    assert kernel_config.get("allow_fallback_vapor", False) is False
    assert executor.config.force_builtin_vapor_pressure is None
    assert raised.value.eval_spec is not None
    assert raised.value.eval_spec.vapor_pressure_provider_id == (
        DEFAULT_VAPOR_PRESSURE_PROVIDER_ID
    )
    assert raised.value.eval_spec.allow_fallback_vapor is False


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


def test_run_reference_derives_real_engine_canonical_class() -> None:
    reference = evaluate_module.RunReference(
        status="ok",
        trace={"backend_status": "ok", "backend_authoritative": True},
        backend_name="alphamelts",
    )

    assert reference.backend_status == "ok"
    assert reference.evidence_class == "melts"
    assert reference.runtime_status == "ok"
    assert reference.backend_real_active is True
    assert reference.certification_allowed is True


def test_run_reference_folds_internal_analytical_alias_to_stub() -> None:
    """Constructing a RunReference with the display alias must not trip the
    fidelity-vocabulary fail-loud (which rejects an unknown `internal-analytical`
    backend token) and must fold to the stable `stub` token / same evidence
    class as the legacy `stub` reference (alias-preserving rebrand)."""
    aliased = evaluate_module.RunReference(
        status="ok",
        trace={"backend_status": "unavailable"},
        backend_name="internal-analytical",
    )
    legacy = evaluate_module.RunReference(
        status="ok",
        trace={"backend_status": "unavailable"},
        backend_name="stub",
    )

    assert aliased.backend_name == "stub"
    assert aliased.evidence_class == legacy.evidence_class
    assert aliased.certification_allowed == legacy.certification_allowed


def test_run_reference_migrates_legacy_no_compared_results_status() -> None:
    reference = evaluate_module.RunReference(
        status="ok",
        trace={"backend_status": "no_compared_results"},
    )

    assert reference.backend_status == "no_compared_results"
    assert reference.runtime_status == "not_run"
    assert reference.degradation_reason == "not_run"
    assert reference.degraded_from == ("not_run",)


def test_run_reference_refuses_spoofed_stub_certification() -> None:
    with pytest.raises(FidelityVocabularyTranslationError):
        evaluate_module.RunReference(
            status="ok",
            backend_name="stub",
            backend_status="ok",
            backend_authoritative=True,
            certification_allowed=True,
        )


def test_run_reference_refuses_conflicting_runtime_status() -> None:
    with pytest.raises(FidelityVocabularyTranslationError):
        evaluate_module.RunReference(
            status="ok",
            backend_name="alphamelts",
            backend_status="ok",
            runtime_status="not_run",
        )


def test_run_reference_unknown_backend_status_fails_closed() -> None:
    with pytest.raises(UnknownFidelityVocabularyTokenError):
        evaluate_module.RunReference(
            status="failed",
            trace={"backend_status": "opaque-status"},
        )
