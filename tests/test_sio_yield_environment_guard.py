from types import SimpleNamespace

import pytest

from simulator import runner


def test_sio_cli_environment_gate_refusal_is_not_golden_drift(
    monkeypatch,
    tmp_path,
):
    fake_execution = {
        "status": "partial",
        "record": {
            "snapshots": [
                {
                    "liquid_fraction_gate": {
                        "redox": {
                            "gate_provider_refusal": {
                                "backend_failure_disposition": "environment",
                                "disposition": "environment",
                                "reason": "engine_worker_timeout",
                            }
                        }
                    }
                }
            ]
        },
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

    with pytest.raises(
        runner.RunnerError,
        match="environment: gate_provider_refusal.*not a golden mismatch",
    ):
        runner.main_sio_yield(
            [
                "--feedstock",
                "lunar_mare_low_ti",
                "--output",
                str(tmp_path / "sio-report.json"),
            ]
        )
