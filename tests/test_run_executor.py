from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from simulator.accounting.ledger import AtomLedger
from simulator.campaigns import (
    CampaignHoldTargetRefusal,
    CampaignPressureSetpointRefusal,
)
from simulator.condensation import KnudsenRegimeRefusal
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.run_executor import (
    RunExecution,
    RunExecutor,
    _aggregate_backend_status,
    _campaigns_elapsed_from_session_history,
)
from simulator.runner import PyrolysisRun
from simulator.session import (
    DecisionPolicy,
    SimSession,
    SimSessionConfig,
    StepResult,
    drive_session,
)
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


def test_run_executor_uses_campaigns_elapsed_override_without_history():
    run = _run(
        hours=0,
        run_metadata_overrides={
            "started_at_utc": "2026-05-30T00:00:00Z",
            "kernel_commit_sha": "run-executor-fixture",
            "campaigns_elapsed": 4,
        }
    )
    config = run._session_config()

    execution = RunExecutor().execute(config)

    assert config.campaigns_elapsed == pytest.approx(4.0)
    assert execution.campaigns_elapsed == pytest.approx(4.0)


def test_campaign_transition_history_overrides_campaign_count_fallback():
    session = SimpleNamespace(
        _step_results=[
            SimpleNamespace(campaign_summary={"campaign": "C0"}),
            SimpleNamespace(campaign_summary=None),
            SimpleNamespace(campaign_summary={"campaign": "C1"}),
        ]
    )

    assert _campaigns_elapsed_from_session_history(
        session,
        fallback=99.0,
    ) == pytest.approx(2.0)


def test_campaign_count_includes_partial_campaign_hours():
    session = SimpleNamespace(
        _step_results=[
            SimpleNamespace(campaign_summary={"campaign": "C0", "duration_h": 8}),
            SimpleNamespace(campaign_summary=None),
            SimpleNamespace(campaign_summary=None),
        ],
        simulator=SimpleNamespace(
            melt=SimpleNamespace(campaign=CampaignPhase.C0B),
            campaign_mgr=SimpleNamespace(_max_hold_hr=lambda _campaign: 8.0),
        ),
    )

    campaigns_elapsed = _campaigns_elapsed_from_session_history(
        session,
        fallback=99.0,
    )

    assert campaigns_elapsed == pytest.approx(1.25)
    assert (8.0 + 2.0) / campaigns_elapsed == pytest.approx(8.0)


def test_campaign_count_resolves_structured_c3_duration():
    campaign_mgr = SimpleNamespace(
        _configured_max_hold_hr=lambda _campaign, phase, path: {
            ("C3_NA", "A_staged"): 3.0,
        }[(phase, path)],
        _campaign_overrides=lambda _campaign: {},
    )
    session = SimpleNamespace(
        _step_results=[SimpleNamespace(campaign_summary=None)],
        simulator=SimpleNamespace(
            melt=SimpleNamespace(campaign=CampaignPhase.C3_NA),
            record=SimpleNamespace(path="A_staged"),
            campaign_mgr=campaign_mgr,
        ),
    )

    assert _campaigns_elapsed_from_session_history(
        session,
        fallback=99.0,
    ) == pytest.approx(1.0 / 3.0)


def test_campaign_count_resolves_structured_c5_branch_max_hold():
    """C5 max_hold_hr is a per-branch mapping; progress must not float() it.

    Authoritative schema (data/setpoints.yaml campaigns.C5.max_hold_hr):
      {branch_two: ..., branch_one: ...}
    Selection rule mirrors campaigns.py C5 endpoint: branch=='two' uses
    branch_two, otherwise branch_one (including unset branch).
    """
    calls: list[tuple] = []

    def _configured_max_hold_hr(_campaign, *path):
        calls.append(path)
        table = {
            ("branch_two",): 800.0,
            ("branch_one",): 400.0,
        }
        return table[path]

    campaign_mgr = SimpleNamespace(
        _configured_max_hold_hr=_configured_max_hold_hr,
        # If the consumer wrongly calls scalar _max_hold_hr(campaign), surface it.
        _max_hold_hr=lambda _campaign: (_ for _ in ()).throw(
            AssertionError("C5 must not resolve scalar max_hold_hr")
        ),
    )

    # Active hour mid-C5 on branch two: 1 completed campaign + 2/800.
    session_two = SimpleNamespace(
        _step_results=[
            SimpleNamespace(campaign_summary={"campaign": "C4"}),
            SimpleNamespace(campaign_summary=None),
            SimpleNamespace(campaign_summary=None),
        ],
        simulator=SimpleNamespace(
            melt=SimpleNamespace(campaign=CampaignPhase.C5),
            record=SimpleNamespace(branch="two"),
            campaign_mgr=campaign_mgr,
        ),
    )
    assert _campaigns_elapsed_from_session_history(
        session_two,
        fallback=99.0,
    ) == pytest.approx(1.0 + 2.0 / 800.0)
    assert calls[-1] == ("branch_two",)

    # Unset branch defaults to branch_one (matches campaigns.py endpoint).
    session_default = SimpleNamespace(
        _step_results=[SimpleNamespace(campaign_summary=None)],
        simulator=SimpleNamespace(
            melt=SimpleNamespace(campaign=CampaignPhase.C5),
            record=SimpleNamespace(branch=""),
            campaign_mgr=campaign_mgr,
        ),
    )
    assert _campaigns_elapsed_from_session_history(
        session_default,
        fallback=99.0,
    ) == pytest.approx(1.0 / 400.0)
    assert calls[-1] == ("branch_one",)

    # Explicit branch one.
    session_one = SimpleNamespace(
        _step_results=[SimpleNamespace(campaign_summary=None)],
        simulator=SimpleNamespace(
            melt=SimpleNamespace(campaign=CampaignPhase.C5),
            record=SimpleNamespace(branch="one"),
            campaign_mgr=campaign_mgr,
        ),
    )
    assert _campaigns_elapsed_from_session_history(
        session_one,
        fallback=99.0,
    ) == pytest.approx(1.0 / 400.0)
    assert calls[-1] == ("branch_one",)


def test_run_metadata_projects_execution_campaign_count_over_override():
    run = _run(
        run_metadata_overrides={
            "started_at_utc": "2026-05-30T00:00:00Z",
            "kernel_commit_sha": "run-executor-fixture",
            "campaigns_elapsed": 99,
        }
    )
    execution = RunExecutor().execute(run._session_config())
    execution = replace(execution, campaigns_elapsed=2.0)

    payload = run._build_output(execution)

    assert payload["run_metadata"]["campaigns_elapsed"] == pytest.approx(2.0)


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


def test_run_executor_keeps_running_after_campaign_scoped_c6_refusal():
    run = _c6_acquisition_refusal_run(
        hours=2,
        setpoints_patch={"campaigns": {"C7": {"enabled": True}}},
    )
    session = SimSession().start(run._session_config())
    session.simulator.record.branch = "two"

    execution = RunExecutor().execute_session(session, hours=2)

    assert execution.status == "ok"
    assert execution.reason == ""
    assert execution.error_message == ""
    assert execution.refusal_diagnostic["status"] == "refused"
    assert execution.refusal_diagnostic["campaign"] == "C6"
    assert (
        execution.refusal_diagnostic["diagnostic"]["reason_refused"]
        == "c6_hold_target_not_acquired"
    )
    assert [row["campaign"] for row in execution.per_hour] == [
        "C6",
        "C7_CA_ALUMINOTHERMIC",
    ]


def test_pyrolysis_run_emits_binding_c6_refusal_diagnostic():
    payload = _c6_acquisition_refusal_run().run()

    assert payload["status"] == "partial"
    assert payload["reason"] == ""
    diagnostic = payload["run_metadata"]["refusal_diagnostic"]
    assert diagnostic["status"] == "refused"
    assert diagnostic["campaign"] == "C6"
    assert diagnostic["diagnostic"]["reason_refused"] == (
        "c6_hold_target_not_acquired"
    )


def test_run_executor_promotes_c4_acquisition_timeout_without_c6_mislabel() -> None:
    config = _run(
        feedstock_id="lunar_mare_low_ti",
        campaign="C4",
        hours=1,
        additives_kg={},
    )._session_config()
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    session = SimSession()
    session.start(config, backend=backend)
    session.simulator.campaign_mgr.campaigns["C4"][
        "max_target_acquisition_hr"
    ] = 1.0

    execution = RunExecutor().execute_session(session, hours=1)

    assert execution.status == "refused"
    assert execution.reason == "c4_target_window_not_acquired"
    assert execution.error_message == execution.reason
    assert execution.refusal_diagnostic["campaign"] == "C4"
    assert execution.refusal_diagnostic["reaction_family"] == (
        "c4_mg_selective_pyrolysis"
    )
    assert execution.refusal_diagnostic["diagnostic"]["reason_refused"] == (
        execution.reason
    )
    assert session.simulator._last_c6_refusal_diagnostic == {}


def test_c4_refusal_is_non_resumable_under_auto_apply_and_decide() -> None:
    """After a typed C4 endpoint refusal, core/session must not expose or
    apply a next campaign decision. AUTO_APPLY and decide('yes') must not
    start C6 (review construction: terminality fail-open)."""
    config = _run(
        feedstock_id="lunar_mare_low_ti",
        campaign="C4",
        hours=1,
        additives_kg={},
    )._session_config()
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    session = SimSession()
    session.start(config, backend=backend)
    # Branch two is the C4→C6 decision path under default-off C5.
    session.simulator.record.branch = "two"
    session.simulator.campaign_mgr.campaigns["C4"][
        "max_target_acquisition_hr"
    ] = 1.0

    execution = RunExecutor().execute_session(session, hours=1)
    assert execution.status == "refused"
    assert execution.reason == "c4_target_window_not_acquired"
    sim = session.simulator
    assert sim._c4_campaign_refused is True
    assert sim.campaign_endpoint_refused() is True
    assert sim.melt.campaign == CampaignPhase.C4
    assert sim.pending_decision is None
    assert sim.paused_for_decision is False
    assert session.pending_decision() is None

    # Another AUTO_APPLY iteration must not create or apply C6_PROCEED.
    list(drive_session(session, hours=2, policy=DecisionPolicy.AUTO_APPLY))
    assert sim.melt.campaign == CampaignPhase.C4
    assert sim.pending_decision is None
    assert sim.paused_for_decision is False
    assert sim._c6_campaign_refused is False
    assert sim._last_c6_refusal_diagnostic == {}

    # Direct decide cannot resume a terminally refused batch either.
    with pytest.raises(RuntimeError, match="non-resumable"):
        session.decide("yes")
    assert sim.melt.campaign == CampaignPhase.C4


def test_nonterminal_c6_refusal_does_not_mask_reporting_failure(
    monkeypatch,
):
    def fail_cost_rollup(**_kwargs):
        raise RuntimeError("cost rollup unavailable")

    monkeypatch.setattr(
        "simulator.run_executor.build_cost_rollup_diagnostic",
        fail_cost_rollup,
    )

    with pytest.raises(RuntimeError, match="cost rollup unavailable"):
        RunExecutor().execute(_c6_acquisition_refusal_run()._session_config())


def test_c6_acquisition_refusal_preserves_completed_tick_and_ledger_accounts():
    payload = _c6_acquisition_refusal_run().run()

    rows = payload["per_hour_summary"]
    # Boilrump: C6 campaign-scoped refusal continues the batch; this fixture
    # is a one-hour C6 acquisition refusal run, not the full CI sequence.
    assert payload["status"] == "partial"
    assert payload["reason"] == ""
    assert len(rows) == 1
    assert rows[0]["campaign"] == "C6"
    assert rows[0]["T_C"] == pytest.approx(25.0)
    assert "process.cleaned_melt" in payload["final_state"]


def test_pyrolysis_run_completes_with_band_adjustment_provenance():
    # The pre-adjudication stranded config (pN2 request below the band) must
    # now run instead of refusing; the substitution is loud in the campaign
    # gas-control diagnostic, not a run-level failure.
    run = _pressure_refusal_run(sio_hold_temperature_c=1600.0)

    payload = run.run()

    assert payload["status"] != "refused"
    assert "refusal_diagnostic" not in payload["run_metadata"]


def test_run_executor_final_budget_pending_decision_is_partial(monkeypatch):
    snapshot = SimpleNamespace()
    simulator = SimpleNamespace(
        atom_ledger=AtomLedger(),
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


def test_run_executor_preserves_nonfinite_c6_hold_refusal(monkeypatch):
    class BareSession:
        simulator = SimpleNamespace()

    diagnostic = {
        "hold_target_C": 1400.0,
        "temperature_C": float("nan"),
        "detail": "C6 hold target and melt temperature must be finite",
    }

    def fail_drive_session(*_args, **_kwargs):
        raise CampaignHoldTargetRefusal(diagnostic)

    monkeypatch.setattr(
        "simulator.run_executor.drive_session",
        fail_drive_session,
    )

    execution = RunExecutor().execute_session(BareSession(), hours=1)

    assert execution.status == "refused"
    assert execution.reason == "c6_hold_target_nonfinite"
    assert execution.error_message == "c6_hold_target_nonfinite"
    assert execution.refusal_diagnostic == {
        **diagnostic,
        "status": "refused",
        "reason": "c6_hold_target_nonfinite",
    }


def _envelope_session() -> object:
    return type("BareSession", (), {"simulator": SimpleNamespace()})()


def test_run_executor_timeout_envelope_keeps_honest_exception(monkeypatch):
    """Mid-run timeout must survive execute_session, not flatten to a bare failed.

    Invert: drop failure_exception and restore backend_status from the
    empty sim default ('ok') and evaluate's failed branch EngineBugAborts.
    drive_session is the production raise site; this is not FakeExecutor.
    """
    from simulator.engine_pool import EngineWorkerTimeout

    timeout = EngineWorkerTimeout("ThermoEngine equilibrium", 3.0, phase="job")

    def fail_drive_session(*_args, **_kwargs):
        raise timeout

    monkeypatch.setattr(
        "simulator.run_executor.drive_session",
        fail_drive_session,
    )

    execution = RunExecutor().execute_session(_envelope_session(), hours=1)

    assert execution.status == "failed"
    assert execution.failure_exception is timeout
    assert execution.backend_status == "not_converged"
    assert execution.backend_status != "unavailable"
    assert type(execution.failure_exception) is EngineWorkerTimeout


def test_run_executor_ood_envelope_keeps_honest_exception(monkeypatch):
    """Mid-run typed OOD must survive execute_session as out_of_domain.

    Invert: envelope-only status=failed with no failure_exception and
    evaluate treats the run as EngineBugAbort.
    """
    from engines.alphamelts.thermoengine import (
        ThermoEngineOutOfDomainError,
        ThermoEngineRefusalCause,
    )

    ood = ThermoEngineOutOfDomainError(
        ThermoEngineRefusalCause.FO2_OUTSIDE_ATTAINABLE_BRACKET,
        requested=-9.0,
    )

    def fail_drive_session(*_args, **_kwargs):
        raise ood

    monkeypatch.setattr(
        "simulator.run_executor.drive_session",
        fail_drive_session,
    )

    execution = RunExecutor().execute_session(_envelope_session(), hours=1)

    assert execution.status == "failed"
    assert execution.failure_exception is ood
    assert execution.backend_status == "out_of_domain"
    assert execution.backend_status != "unavailable"


def test_run_executor_importerror_envelope_is_still_absence(monkeypatch):
    """Genuine missing library through the envelope is REPORTED as absence.

    ★ THIS GUARD USED TO PROVE ONLY THE NEGATIVE. It asserted
        backend_status != "not_converged"
        backend_status != "out_of_domain"
    which forbids RELABELLING absence as an honest engine answer -- correct as
    far as it goes -- but never REQUIRED `unavailable`. So the flattering
    default `ok` passed a test written to prevent exactly this, and a genuine
    missing library was serialized by the runner as a healthy engine.

    A guard that proves what a value is NOT, and never what it IS, leaves the
    default as an unchecked answer. The positive assertion is the one that
    matters.
    """
    missing = ImportError("No module named 'thermoengine'")

    def fail_drive_session(*_args, **_kwargs):
        raise missing

    monkeypatch.setattr(
        "simulator.run_executor.drive_session",
        fail_drive_session,
    )

    execution = RunExecutor().execute_session(_envelope_session(), hours=1)

    assert execution.status == "failed"
    assert execution.failure_exception is missing
    assert execution.backend_status == "unavailable"
    # kept: absence must still never be relabelled as an honest engine answer
    assert execution.backend_status != "not_converged"
    assert execution.backend_status != "out_of_domain"


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
        atom_ledger=AtomLedger(),
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


def _c6_acquisition_refusal_run(**overrides) -> PyrolysisRun:
    options = {
        "feedstock_id": "lunar_mare_low_ti",
        "campaign": "C6",
        "hours": 3,
        "runtime_campaign_overrides": {
            "C6": {"max_hours": 1, "ramp_rate_C_per_hr": 0},
        },
    }
    options.update(overrides)
    return _run(**options)


def _ledger_mol_by_account(simulator: object) -> dict[str, dict[str, float]]:
    ledger = simulator.atom_ledger.mol_by_account()
    return {
        str(account): {
            str(species): float(mol)
            for species, mol in sorted(species_mol.items())
        }
        for account, species_mol in sorted(ledger.items())
    }


def _inject_hostile_melt_resistance(sim) -> None:
    setpoints = dict(getattr(sim, "setpoints", {}) or {})
    kernel = dict(setpoints.get("chemistry_kernel", {}) or {})
    series = dict(kernel.get("evaporation_series_resistance", {}) or {})
    series["melt_resistance_enabled"] = True
    series["melt_surface_renewal_base_kg_s_m2_pa"] = 1.0e-4
    kernel["evaporation_series_resistance"] = series
    setpoints["chemistry_kernel"] = kernel
    sim.setpoints = setpoints


def _patch_hostile_melt_on_evaporation(monkeypatch) -> None:
    """Ensure hostile config is live whenever flux projection runs."""
    from simulator.core import PyrolysisSimulator

    real = PyrolysisSimulator._calculate_evaporation

    def wrapped(self, *args, **kwargs):
        _inject_hostile_melt_resistance(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(
        PyrolysisSimulator, "_calculate_evaporation", wrapped
    )


def test_run_executor_refuses_uncertified_melt_resistance_model(monkeypatch):
    """FIX1: hostile melt_resistance_enabled is a typed run refusal.

    Projection path: provider status=refused → EvaporationFluxRefusal →
    run_executor status=refused with persisted refusal_diagnostic.
    """
    _patch_hostile_melt_on_evaporation(monkeypatch)

    execution = RunExecutor().execute(_run(hours=8)._session_config())

    assert execution.status == "refused"
    assert execution.reason == "uncertified_melt_resistance_model"
    assert execution.refusal_diagnostic["reason"] == (
        "uncertified_melt_resistance_model"
    )
    assert execution.refusal_diagnostic.get("evaporation_flux_kg_hr", {}) == {}


def test_pyrolysis_run_emits_uncertified_melt_resistance_refusal_diagnostic(
    monkeypatch,
):
    """End-to-end PyrolysisRun path persists refusal_diagnostic."""
    _patch_hostile_melt_on_evaporation(monkeypatch)

    payload = _run(hours=8).run()

    assert payload["status"] == "refused"
    assert payload["reason"] == "uncertified_melt_resistance_model"
    diagnostic = payload["run_metadata"]["refusal_diagnostic"]
    assert diagnostic["reason"] == "uncertified_melt_resistance_model"
    assert diagnostic.get("evaporation_flux_kg_hr", {}) == {}


def _c4_point_two_mbar_transitional_run() -> PyrolysisRun:
    return PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C4",
        hours=1,
        mass_kg=1000,
        backend_name="internal-analytical",
        setpoints_patch={"furnace_max_T_C": 1200},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
        run_metadata_overrides={
            "started_at_utc": "2026-08-11T00:00:00Z",
            "kernel_commit_sha": "t470-transitional-refusal",
        },
    )


@pytest.mark.xdist_group("serial")
def test_c4_transitional_flux_refusal_is_visible_and_preserves_ledger_closure():
    run = _c4_point_two_mbar_transitional_run()
    session = run._start_session()
    sim = session.simulator
    sim.melt.temperature_C = 1200.0
    ledger_before = _ledger_mol_by_account(sim)
    transitions_before = tuple(sim.atom_ledger.transitions)
    drift_before = sim.atom_ledger.element_atom_drift_report()

    payload = run._run_session(session)

    assert payload["status"] == "refused"
    assert payload["reason"] == "viscous_p_bulk_transport_out_of_domain"
    assert payload["run_metadata"]["hours_requested"] == 1
    assert payload["run_metadata"]["hours_completed"] == 0
    assert payload["per_hour_summary"] == []

    diagnostic = payload["run_metadata"]["refusal_diagnostic"]
    assert diagnostic["evaporation_flux_status"] == "not_evaluated"
    assert diagnostic["evaporation_flux_kg_hr"] is None
    assert 0.01 <= diagnostic["knudsen_number"] < 10.0
    assert diagnostic["commanded_pressure_mbar"] == pytest.approx(0.2)
    assert "Mg" in diagnostic["affected_species"]
    assert diagnostic["campaign_name"] == "C4"
    assert diagnostic["process_regime"] == "pyrolysis_extraction"
    assert diagnostic["asking_site"] == "engines.builtin.evaporation_flux"
    assert diagnostic["stage"] == "C4"

    assert _ledger_mol_by_account(sim) == ledger_before
    assert tuple(sim.atom_ledger.transitions) == transitions_before
    assert sim.atom_ledger.element_atom_drift_report() == drift_before
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12


@pytest.mark.xdist_group("serial")
def test_finite_capacity_preserves_transitional_evaporation_refusal(monkeypatch):
    from simulator.thermal_train import (
        FiniteCapacity,
        thermal_train_parameters_from_mapping,
    )

    params = thermal_train_parameters_from_mapping()
    assert params.cold_train is not None
    enforced = replace(
        params,
        cold_train=replace(params.cold_train, runtime_enforcement=True),
    )
    monkeypatch.setattr(
        "simulator.thermal_train.thermal_train_parameters_from_mapping",
        lambda: enforced,
    )

    run = _c4_point_two_mbar_transitional_run()
    session = run._start_session()
    session.simulator.melt.temperature_C = 1200.0
    sim = session.simulator
    capacity, _cold_train = session.simulator._cold_train_capacity_policy()
    assert isinstance(capacity, FiniteCapacity)
    ledger_before = _ledger_mol_by_account(sim)
    transitions_before = tuple(sim.atom_ledger.transitions)
    drift_before = sim.atom_ledger.element_atom_drift_report()

    payload = run._run_session(session)

    assert payload["status"] == "refused"
    assert payload["reason"] == "viscous_p_bulk_transport_out_of_domain"
    diagnostic = payload["run_metadata"]["refusal_diagnostic"]
    assert diagnostic["evaporation_flux_status"] == "not_evaluated"
    assert diagnostic["evaporation_flux_kg_hr"] is None
    assert _ledger_mol_by_account(sim) == ledger_before
    assert tuple(sim.atom_ledger.transitions) == transitions_before
    assert sim.atom_ledger.element_atom_drift_report() == drift_before
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5e-12


def test_native_fe_helper_maps_melt_resistance_to_typed_refusal():
    """core/extraction helper consumers map config errors to typed refusal."""
    from engines.builtin.evaporation_flux import EvaporationFluxConfigurationError
    from simulator.evaporation import (
        EvaporationFluxRefusal,
        evaporation_flux_refusal_from_configuration_error,
    )

    mapped = evaporation_flux_refusal_from_configuration_error(
        EvaporationFluxConfigurationError(
            "authoritative melt resistance requires species- and state-specific "
            "D_i, k_L,i, and dp_eq/dC_i; universal pressure conductance disabled"
        )
    )
    assert isinstance(mapped, EvaporationFluxRefusal)
    assert mapped.reason == "uncertified_melt_resistance_model"
    assert mapped.diagnostic["reason"] == "uncertified_melt_resistance_model"


def test_provider_projection_raises_on_uncertified_melt_resistance():
    """Projection path: non-OK provider result becomes EvaporationFluxRefusal."""
    from engines.builtin.evaporation_flux import BuiltinEvaporationFluxProvider
    from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
    from simulator.chemistry.kernel.dto import ProviderAccountView
    from simulator.evaporation import EvaporationFluxRefusal

    provider = BuiltinEvaporationFluxProvider()
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.EVAPORATION_FLUX,
            account_view=ProviderAccountView(
                accounts={"process.cleaned_melt": {"Na2O": 1.0}},
                species_formula_registry={},
            ),
            temperature_C=1500.0,
            pressure_bar=1e-6,
            fO2_log=None,
            control_inputs={
                "vapor_pressures_Pa": {"Na": 100.0},
                "overhead_partials_Pa": {},
                "overhead_pressure_pa": 0.0,
                "molar_mass_kg_mol": {"Na": 0.023},
                "stoich_by_species": {
                    "Na": {
                        "parent_oxide": "Na2O",
                        "oxide_per_product_kg": 1.347,
                        "O2_per_product_kg": 0.347,
                    }
                },
                "available_oxide_kg": {"Na": 10.0},
                "melt_surface_area_m2": 0.2,
                "stir_factor": 1.0,
                "alpha": 0.5,
                "evaporation_series_resistance": {
                    "melt_resistance_enabled": True,
                    "melt_surface_renewal_base_kg_s_m2_pa": 1.0e-4,
                },
            },
        )
    )
    assert result.status == "refused"
    assert result.diagnostic["reason"] == "uncertified_melt_resistance_model"

    with pytest.raises(EvaporationFluxRefusal) as ei:
        if str(result.status) != "ok":
            raise EvaporationFluxRefusal(
                str(result.diagnostic.get("reason")),
                dict(result.diagnostic),
            )
    assert ei.value.reason == "uncertified_melt_resistance_model"


def test_typed_failure_does_not_overrule_a_recorded_unavailable(monkeypatch):
    """b-264: a typed failure is a candidate for the ranking, not a replacement.

    ★ THIS TEST EXISTS AT THE CALL SITE ON PURPOSE. The defect lived in the
    caller --

        backend_status = _aggregate_backend_status(history, latest)
        if honest is not None:
            backend_status = honest        # selection discarded

    -- so a unit test on `_aggregate_backend_status` alone could pin the new
    contract but could never have caught the original bug. The sibling
    envelope tests above do not catch it either: they use a bare simulator
    whose history is empty, where the typed token wins under precedence too,
    so the override and the candidate are indistinguishable. A RECORDED
    `unavailable` in the history is what separates them.

    Direction that matters: `out_of_domain` prunes the candidate permanently as
    a physics verdict about the recipe, while `unavailable` means the engine
    was missing and the candidate deserves a retry. Overruled, a broken install
    became a permanent conclusion about the process being designed.
    """
    from engines.alphamelts.thermoengine import (
        ThermoEngineOutOfDomainError,
        ThermoEngineRefusalCause,
    )

    ood = ThermoEngineOutOfDomainError(
        ThermoEngineRefusalCause.FO2_OUTSIDE_ATTAINABLE_BRACKET,
        requested=-9.0,
    )

    def fail_drive_session(*_args, **_kwargs):
        raise ood

    monkeypatch.setattr("simulator.run_executor.drive_session", fail_drive_session)

    session = type(
        "AbsentEngineSession",
        (),
        {
            "simulator": SimpleNamespace(
                _backend_status_history=("unavailable",),
                _last_backend_status="unavailable",
            )
        },
    )()

    execution = RunExecutor().execute_session(session, hours=1)

    assert execution.failure_exception is ood
    assert execution.backend_status == "unavailable"
    assert execution.backend_status != "out_of_domain"


def test_unclassifiable_exception_does_not_mint_engine_absence(monkeypatch):
    """Failing to CLASSIFY an exception must not be reported as a missing engine.

    `_backend_status_from_honest_exception` stringifies the exception to
    classify it. An exception whose `__str__` itself raises therefore breaks the
    classifier -- and if that call sits inside the degraded envelope's
    `_safe_str(..., "unavailable")` boundary, the failure is swallowed and the
    run reports the FACTUAL token `unavailable` with no absence evidence behind
    it. The optimizer then raises BackendUnavailableAbort, cancels the batch and
    leaves the candidate for retry, when the real event was an unclassified
    programming failure that should stay loud.

    ★ NOTE THE EXCEPTION TYPE. The sibling
    test_run_executor_failure_envelope_uses_safe_exception_text raises
    RuntimeError, which the ThermoEngine membership helpers catch themselves --
    so it never reaches the classifier and cannot see this path. A non-RuntimeError
    is required, which is why this test exists separately rather than as another
    case of that one.
    """

    class _BadStr(Exception):
        def __str__(self) -> str:
            raise ValueError("stringifying this exception fails")

    boom = _BadStr()

    def fail_drive_session(*_args, **_kwargs):
        raise boom

    monkeypatch.setattr("simulator.run_executor.drive_session", fail_drive_session)

    session = type(
        "HealthyHistorySession",
        (),
        {
            "simulator": SimpleNamespace(
                _backend_status_history=("ok",),
                _last_backend_status="ok",
            )
        },
    )()

    execution = RunExecutor().execute_session(session, hours=1)

    # the run failed, and says so
    assert execution.status == "failed"
    # ...but the engine was never absent, and must not be reported as such
    assert execution.backend_status != "unavailable"
