from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from simulator.campaigns import CampaignPressureSetpointRefusal
from simulator.condensation import KnudsenRegimeRefusal
from simulator.run_executor import RunExecution, RunExecutor, _aggregate_backend_status
from simulator.runner import PyrolysisRun
from simulator.session import SimSession, SimSessionConfig, StepResult
from simulator.state import CampaignPhase, DecisionType
from simulator.trace import PhysicsTrace


def _run(**overrides) -> PyrolysisRun:
    kwargs = {
        "feedstock_id": "mars_basalt",
        "campaign": "C2A",
        "hours": 2,
        "additives_kg": {"C": 30.0},
        "allow_fallback_vapor": True,
        "allow_unmeasured_alpha_fallback": True,
        "run_metadata_overrides": {
            "started_at_utc": "2026-05-30T00:00:00Z",
            "kernel_commit_sha": "run-executor-fixture",
        },
    }
    kwargs.update(overrides)
    return PyrolysisRun(**kwargs)


def test_run_executor_returns_structured_execution():
    execution = RunExecutor().execute(_run()._session_config())

    assert isinstance(execution, RunExecution)
    assert execution.status == "ok"
    assert execution.error_message == ""
    assert execution.reason == ""
    assert execution.snapshots
    assert len(execution.per_hour) == len(execution.snapshots)
    assert isinstance(execution.trace, PhysicsTrace)
    assert execution.trace.snapshots == execution.snapshots
    assert isinstance(execution.operator_decisions, tuple)


def test_pyrolysis_run_is_executor_json_adapter():
    run = _run()
    execution = RunExecutor().execute(run._session_config())

    assert run._build_output(execution) == _run().run()


# SC-67 adjudication (t-185 x wave-06-pressure): an out-of-band configured
# p_total now ADJUSTS to the band edge with provenance (see
# test_campaign_pressure_defaults), so the typed refusal — and the runner
# envelope plumbing these tests exercise — fires only for the genuinely
# EMPTY/INVALID band, simulated here by inverting the band constants.
def _invert_pn2_band(monkeypatch):
    import simulator.campaigns as campaigns_module

    monkeypatch.setattr(
        campaigns_module, "C2A_STAGED_PN2_SWEEP_MIN_MBAR", 15.0
    )
    monkeypatch.setattr(
        campaigns_module, "C2A_STAGED_PN2_SWEEP_MAX_MBAR", 5.0
    )


def test_run_executor_preserves_campaign_pressure_refusal_during_startup(
    monkeypatch,
):
    _invert_pn2_band(monkeypatch)
    run = _pressure_refusal_run()

    execution = RunExecutor().execute(run._session_config())

    assert execution.status == "refused"
    assert execution.reason == "c2a_staged_pn2_outside_operating_band"
    assert execution.error_message.startswith(execution.reason)
    assert execution.refusal_diagnostic["detail"] == (
        "pN2 sweep operating band is empty or invalid"
    )
    assert execution.refusal_diagnostic["allowed_pN2_mbar"] == [15.0, 5.0]


def test_pyrolysis_run_emits_campaign_pressure_refusal_diagnostic(monkeypatch):
    _invert_pn2_band(monkeypatch)
    run = _pressure_refusal_run(sio_hold_temperature_c=1600.0)

    payload = run.run()

    assert payload["status"] == "refused"
    assert payload["reason"] == "c2a_staged_pn2_outside_operating_band"
    diagnostic = payload["run_metadata"]["refusal_diagnostic"]
    assert diagnostic["reason"] == payload["reason"]
    assert diagnostic["detail"] == (
        "pN2 sweep operating band is empty or invalid"
    )
    assert diagnostic["allowed_pN2_mbar"] == [15.0, 5.0]
    assert "knudsen_regime_diagnostic" not in payload["run_metadata"]


def test_run_executor_promotes_binding_c6_refusal_from_campaign_summary():
    execution = RunExecutor().execute(_c6_refusal_run()._session_config())

    assert execution.status == "refused"
    assert execution.reason == (
        "c6_joint_thermodynamic_liquid_fraction_window_empty"
    )
    assert execution.error_message == execution.reason
    assert execution.refusal_diagnostic["status"] == "refused"
    assert execution.refusal_diagnostic["campaign"] == "C6"
    assert (
        execution.refusal_diagnostic["diagnostic"]["reason_refused"]
        == execution.reason
    )


def test_pyrolysis_run_emits_binding_c6_refusal_diagnostic():
    payload = _c6_refusal_run().run()

    assert payload["status"] == "refused"
    assert payload["reason"] == (
        "c6_joint_thermodynamic_liquid_fraction_window_empty"
    )
    diagnostic = payload["run_metadata"]["refusal_diagnostic"]
    assert diagnostic["status"] == "refused"
    assert diagnostic["campaign"] == "C6"
    assert diagnostic["diagnostic"]["reason_refused"] == payload["reason"]


def test_run_executor_degraded_envelope_preserves_binding_c6_refusal(
    monkeypatch,
):
    def fail_cost_rollup(**_kwargs):
        raise RuntimeError("cost rollup unavailable")

    monkeypatch.setattr(
        "simulator.run_executor.build_cost_rollup_diagnostic",
        fail_cost_rollup,
    )

    execution = RunExecutor().execute(_c6_refusal_run()._session_config())

    assert execution.status == "refused"
    assert execution.reason == (
        "c6_joint_thermodynamic_liquid_fraction_window_empty"
    )
    assert execution.refusal_diagnostic["status"] == "refused"
    assert "envelope detail unavailable" in execution.envelope_detail_unavailable


def test_ci_c0_to_c6_refusal_preserves_prior_rows_and_ledger_accounts():
    payload = _run(
        feedstock_id="ci_carbonaceous_chondrite",
        campaign="C0",
        hours=500,
    ).run()

    rows = payload["per_hour_summary"]
    assert payload["status"] == "refused"
    assert payload["reason"] == (
        "c6_joint_thermodynamic_liquid_fraction_window_empty"
    )
    # The binding C6 refusal now fires on the first C6 tick, after all prior
    # campaign rows have been preserved in the envelope.
    assert len(rows) == 42
    assert list(dict.fromkeys(row["campaign"] for row in rows)) == [
        "C0",
        "C0B",
        "C2A_STAGED",
        "C3_NA",
        "C4",
        "C6",
    ]
    # Preservation contract: the pre-refusal campaigns' accounts survive the
    # C6 refusal. Subset, not equality — additional accounts appearing as
    # upstream chemistry fixes let MORE of the sequence execute (e.g.
    # process.metal_phase once the Mg rail boundary landed) are legitimate.
    assert set(payload["final_state"]) >= {
        "process.cleaned_melt",
        "process.condensation_train",
        "process.overhead_gas",
        "process.reagent_inventory",
        "process.stage0_volatile_feed",
        "process.wall_deposit_segment_stage_0_to_stage_1",
        "process.wall_deposit_segment_stage_1_to_stage_2",
        "reservoir.fo2_buffer",
        "reservoir.reagent.C",
        "reservoir.stage0_oxidant",
        "terminal.offgas",
        "terminal.oxygen_melt_offgas_stored",
        "terminal.stage0_salt_phase",
        "terminal.stage0_sulfide_matte",
    }
    assert set(payload) == set(_run().run())


def test_pyrolysis_run_completes_with_band_adjustment_provenance():
    # The pre-adjudication stranded config (pN2 request below the band) must
    # now run instead of refusing; the substitution is loud in the campaign
    # gas-control diagnostic, not a run-level failure.
    run = _pressure_refusal_run(sio_hold_temperature_c=1600.0)

    payload = run.run()

    assert payload["status"] != "refused"
    assert "refusal_diagnostic" not in payload["run_metadata"]


def test_run_executor_partial_path_sets_status_and_decisions():
    run = _run(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=500,
        additives_kg={},
    )

    execution = RunExecutor().execute(run._session_config())

    assert execution.status == "partial"
    assert execution.error_message == ""
    assert execution.operator_decisions
    assert execution.shadow_trace == execution.operator_decisions


def test_run_executor_final_budget_pending_decision_is_partial(monkeypatch):
    snapshot = SimpleNamespace()
    simulator = SimpleNamespace(
        record=SimpleNamespace(snapshots=(snapshot,)),
        cost_ledger=SimpleNamespace(),
        product_ledger=lambda: {},
        melt=SimpleNamespace(hour=1),
    )

    def pending_decision(_self):
        return SimpleNamespace()

    BareSession = type(
        "BareSession",
        (),
        {"simulator": simulator, "pending_decision": pending_decision},
    )

    def one_step(*_args, **_kwargs):
        yield StepResult(snapshot=snapshot, per_hour_summary={"hour": 1})

    monkeypatch.setattr("simulator.run_executor.drive_session", one_step)
    monkeypatch.setattr(
        "simulator.run_executor.build_cost_rollup_diagnostic",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "simulator.run_executor.pumping_context_from_sim",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        PhysicsTrace,
        "from_simulator",
        classmethod(lambda cls, _sim: cls(snapshots=(snapshot,))),
    )

    execution = RunExecutor().execute_session(BareSession(), hours=1)

    assert execution.status == "partial"
    assert execution.reason == "pending_decision"


@pytest.mark.parametrize(
    "feedstock_id",
    (
        "lunar_mare_low_ti",
        "targeted_super_kreep_ore",
    ),
)
def test_run_executor_stop_at_stage0_exit_is_ok_for_real_and_synthetic_feedstocks(
    feedstock_id: str,
) -> None:
    config = replace(
        PyrolysisRun(
            feedstock_id=feedstock_id,
            campaign="C0",
            hours=500,
            allow_fallback_vapor=True,
            allow_unmeasured_alpha_fallback=True,
            run_metadata_overrides={
                "started_at_utc": "2026-06-17T00:00:00Z",
                "kernel_commit_sha": "stage0-stop-test",
            },
        )._session_config(),
        stop_at_stage0_exit=True,
    )

    execution = RunExecutor().execute(config)

    assert execution.status == "ok"
    assert execution.reason == "stage0_exit"
    assert execution.error_message == ""
    assert execution.simulator.melt.campaign is CampaignPhase.C0B
    assert execution.simulator.pending_decision is not None
    assert execution.simulator.pending_decision.decision_type is DecisionType.PATH_AB
    assert execution.simulator.melt.hour < 500


def test_run_executor_stage0_stop_ledger_matches_pre_path_ab_c0b_cut() -> None:
    config = replace(
        PyrolysisRun(
            feedstock_id="lunar_mare_low_ti",
            campaign="C0",
            hours=500,
            allow_fallback_vapor=True,
            allow_unmeasured_alpha_fallback=True,
            run_metadata_overrides={
                "started_at_utc": "2026-06-17T00:00:00Z",
                "kernel_commit_sha": "stage0-stop-parity",
            },
        )._session_config(),
        stop_at_stage0_exit=True,
    )
    expected = _pre_path_ab_c0b_ledger(config)

    execution = RunExecutor().execute(config)
    actual = _ledger_mol_by_account(execution.simulator)

    assert execution.status == "ok"
    assert actual.keys() == expected.keys()
    for account, expected_species in expected.items():
        assert actual[account].keys() == expected_species.keys()
        for species, expected_mol in expected_species.items():
            assert actual[account][species] == pytest.approx(
                expected_mol,
                rel=0.0,
                abs=1.0e-9,
            )


def test_backend_status_aggregation_preserves_recovered_domain_edges():
    assert _aggregate_backend_status(("ok", "out_of_domain", "ok"), "ok") == (
        "out_of_domain"
    )
    assert _aggregate_backend_status(("ok", "not_converged"), "ok") == (
        "not_converged"
    )
    assert _aggregate_backend_status(("ok",), "ok") == "ok"


def test_run_executor_degraded_envelope_preserves_refusal(monkeypatch):
    class BareSession:
        simulator = SimpleNamespace()

    def fail_drive_session(*_args, **_kwargs):
        raise KnudsenRegimeRefusal(
            {
                "status": "refused",
                "reason": "knudsen_outside_viscous_flow",
                "segments": [{"regime": "free_molecular"}],
            }
        )

    monkeypatch.setattr(
        "simulator.run_executor.drive_session",
        fail_drive_session,
    )

    execution = RunExecutor().execute_session(BareSession(), hours=1)

    assert execution.status == "refused"
    assert execution.reason == "knudsen_outside_viscous_flow"
    assert execution.error_message == "knudsen_outside_viscous_flow"
    assert execution.refusal_diagnostic == {
        "status": "refused",
        "reason": "knudsen_outside_viscous_flow",
        "segments": [{"regime": "free_molecular"}],
    }
    assert "envelope detail unavailable" in execution.envelope_detail_unavailable


def test_run_executor_preserves_campaign_pressure_refusal_during_execution(
    monkeypatch,
):
    class BareSession:
        simulator = SimpleNamespace()

    diagnostic = {
        "status": "refused",
        "reason": "c2a_staged_pn2_outside_operating_band",
        "requested_pN2_mbar": 20.0,
        "allowed_pN2_mbar": [5.0, 15.0],
    }

    def fail_drive_session(*_args, **_kwargs):
        raise CampaignPressureSetpointRefusal(diagnostic)

    monkeypatch.setattr(
        "simulator.run_executor.drive_session",
        fail_drive_session,
    )

    execution = RunExecutor().execute_session(BareSession(), hours=1)

    assert execution.status == "refused"
    assert execution.reason == diagnostic["reason"]
    assert execution.error_message == diagnostic["reason"]
    assert execution.refusal_diagnostic == diagnostic


def test_run_executor_failure_envelope_uses_safe_exception_text(monkeypatch):
    class BadStr(Exception):
        def __str__(self):
            raise RuntimeError("secondary string failure")

    class BareSession:
        simulator = SimpleNamespace()

    def fail_drive_session(*_args, **_kwargs):
        raise BadStr()

    monkeypatch.setattr(
        "simulator.run_executor.drive_session",
        fail_drive_session,
    )

    execution = RunExecutor().execute_session(BareSession(), hours=1)

    assert execution.status == "failed"
    assert execution.error_message == (
        "BadStr: <message unavailable: RuntimeError>"
    )


def test_run_executor_poison_enrichment_survives_rollup_failure(monkeypatch):
    poisoned = SimpleNamespace(
        hour=3,
        committed_transition_count=2,
        aborting_exception_summary="projection failed",
    )
    simulator = SimpleNamespace(
        _poisoned_hour=poisoned,
        record=SimpleNamespace(snapshots=()),
        cost_ledger=SimpleNamespace(),
        product_ledger=lambda: {},
        melt=SimpleNamespace(hour=1),
    )

    BareSession = type("BareSession", (), {"simulator": simulator})

    monkeypatch.setattr(
        "simulator.run_executor.drive_session",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        "simulator.run_executor.pumping_context_from_sim",
        lambda *_args, **_kwargs: {},
    )

    def fail_rollup(*_args, **_kwargs):
        raise RuntimeError("rollup unavailable")

    monkeypatch.setattr(
        "simulator.run_executor.build_cost_rollup_diagnostic",
        fail_rollup,
    )

    execution = RunExecutor().execute_session(BareSession(), hours=1)

    assert execution.status == "failed"
    assert execution.reason == "poisoned_hour"
    assert execution.error_message.startswith("PoisonedHourError:")
    assert "envelope detail unavailable" in execution.envelope_detail_unavailable


def test_run_executor_rejects_negative_hours_before_stepping():
    session = SimSession().start(_run()._session_config())
    before_hour = session.simulator.melt.hour

    with pytest.raises(ValueError, match="hours must be non-negative"):
        RunExecutor().execute_session(session, hours=-1)

    assert session.simulator.melt.hour == before_hour


def test_run_executor_slices_resumed_session_snapshots_to_execution_window():
    session = SimSession().start(_run()._session_config())
    session.advance()
    snapshot_start = len(session.simulator.record.snapshots)

    execution = RunExecutor().execute_session(session, hours=1)

    assert len(execution.per_hour) == 1
    assert execution.snapshots == tuple(session.simulator.record.snapshots[snapshot_start:])
    assert len(execution.snapshots) == 1
    assert execution.trace.snapshots == execution.snapshots


def _pressure_refusal_run(**overrides) -> PyrolysisRun:
    base = _run(campaign="C2A_staged", hours=1)
    stages = deepcopy(
        base._session_config().setpoints["campaigns"]["C2A_staged"]["stages"]
    )
    stages[0].update({
        "gas_cover_mode": "pn2_sweep",
        "pO2_mbar": 0.25,
        "p_total_mbar": 1.25,
    })
    return _run(
        campaign="C2A_staged",
        hours=1,
        setpoints_patch={
            "campaigns": {"C2A_staged": {"stages": stages}},
        },
        **overrides,
    )


def _c6_refusal_run() -> PyrolysisRun:
    return _run(
        feedstock_id="ci_carbonaceous_chondrite",
        campaign="C6",
        hours=1,
        additives_kg={},
    )


def _pre_path_ab_c0b_ledger(
    config: SimSessionConfig,
) -> dict[str, dict[str, float]]:
    session = SimSession().start(config)
    for _ in range(500):
        decision = session.pending_decision()
        if (
            decision is not None
            and decision.decision_type is DecisionType.PATH_AB
            and session.simulator.melt.campaign is CampaignPhase.C0B
        ):
            return _ledger_mol_by_account(session.simulator)
        session.advance()
    raise AssertionError("Stage-0 C0B PATH_AB boundary not reached")


def _ledger_mol_by_account(simulator: object) -> dict[str, dict[str, float]]:
    ledger = simulator.atom_ledger.mol_by_account()
    return {
        str(account): {
            str(species): float(mol)
            for species, mol in sorted(species_mol.items())
        }
        for account, species_mol in sorted(ledger.items())
    }
