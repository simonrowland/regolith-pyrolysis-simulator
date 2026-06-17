from __future__ import annotations

from dataclasses import replace

import pytest

from simulator.run_executor import RunExecution, RunExecutor, _aggregate_backend_status
from simulator.runner import PyrolysisRun
from simulator.session import SimSession, SimSessionConfig
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
