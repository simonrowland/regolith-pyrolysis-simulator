from simulator.accounting.run_artifact import build_run_artifact


def _runner_payload() -> dict:
    summaries = [
        {"hour": 1, "campaign": "C0", "mass_balance_pct": 0.0},
        {"hour": 2, "campaign": "C1", "mass_balance_pct": 0.0},
    ]
    return {
        "status": "ok",
        "run_metadata": {},
        "per_hour_summary": summaries,
        "per_hour_ledger": {
            "1": {
                "process.cleaned_melt": {"SiO2": 12.5},
                "terminal.oxygen_stage0_stored": {"O2": 3.25},
            }
        },
    }


def test_timestep_ledger_is_optional_and_summary_stays_verbatim() -> None:
    payload = _runner_payload()
    artifact = build_run_artifact(payload, run_id="ledger-run")

    assert artifact["timesteps"][0]["summary"] is payload["per_hour_summary"][0]
    assert artifact["timesteps"][0]["ledger"] == {
        "process.cleaned_melt": {"SiO2": 12.5},
        "terminal.oxygen_stage0_stored": {"O2": 3.25},
    }
    assert "ledger" not in artifact["timesteps"][1]


def test_timestep_ledger_is_a_detached_mol_native_copy() -> None:
    payload = _runner_payload()
    artifact = build_run_artifact(payload, run_id="ledger-copy")

    payload["per_hour_ledger"]["1"]["process.cleaned_melt"]["SiO2"] = 99.0

    ledger = artifact["timesteps"][0]["ledger"]
    assert ledger["process.cleaned_melt"]["SiO2"] == 12.5
    assert set(ledger) == {
        "process.cleaned_melt",
        "terminal.oxygen_stage0_stored",
    }
    assert "kg_by_account" not in ledger
