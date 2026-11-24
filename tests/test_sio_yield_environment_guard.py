import json
from types import SimpleNamespace

import pytest

from simulator import runner
from tests.condensation_test_support import assert_no_sio_gate_environment_holds


def test_sio_cli_projects_environment_gate_hold_and_completes(
    monkeypatch,
    tmp_path,
):
    fake_execution = {
        "status": "partial",
        "per_hour_summary": [
            {
                "hour": 4,
                "campaign": "C2A",
                "oxygen_reservoir": {
                    "liquid_fraction_gate": {
                        "redox": {
                            "gate_provider_refusal": {
                                "backend_failure_disposition": "environment",
                                "disposition": "environment",
                                "reason": "engine_worker_timeout",
                            }
                        }
                    }
                },
            }
        ],
        "final_state": {
            "status": "unavailable",
            "section": "fake_sio_execution",
        },
    }
    fake_simulator = SimpleNamespace(
        melt=SimpleNamespace(temperature_C=1450.0),
        atom_ledger=SimpleNamespace(
            project_account_mol=lambda _account: {"SiO2": 1.0}
        ),
    )
    fake_session = SimpleNamespace(simulator=fake_simulator)

    monkeypatch.setattr(
        runner.PyrolysisRun,
        "_start_session",
        lambda _run: fake_session,
    )
    monkeypatch.setattr(
        runner.PyrolysisRun,
        "_run_session",
        lambda _run, _session: fake_execution,
    )
    monkeypatch.setattr(
        runner,
        "_prepare_sio_campaign_start",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "_apply_sio_wall_sweep_controls",
        lambda *_args, **_kwargs: None,
    )

    output_path = tmp_path / "sio-report.json"
    assert runner.main_sio_yield(
        [
            "--feedstock",
            "lunar_mare_low_ti",
            "--output",
            str(output_path),
        ]
    ) == 0
    report = json.loads(output_path.read_text())
    assert report["gate_environment_holds"] == [
        {
            "hour": 4,
            "campaign": "C2A",
            "reason": "engine_worker_timeout",
            "disposition": "environment",
        }
    ]


def test_sio_golden_evidence_refuses_environment_gate_hold():
    from simulator.reduced_real_determinism import ENVIRONMENT_GATE_HOLD_MESSAGE

    report = {
        "gate_environment_holds": [
            {
                "hour": 4,
                "campaign": "C2A",
                "reason": "engine_worker_timeout",
                "disposition": "environment",
            }
        ]
    }
    with pytest.raises(AssertionError) as error:
        assert_no_sio_gate_environment_holds(report)
    assert str(error.value) == ENVIRONMENT_GATE_HOLD_MESSAGE
