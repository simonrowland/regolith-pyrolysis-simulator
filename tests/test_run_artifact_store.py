from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from flask import Flask

from simulator.accounting.run_artifact import (
    ARTIFACT_SCHEMA_VERSION,
    RunArtifactContractError,
    build_run_artifact,
)
from web import routes as web_routes
from web.run_store import RunArtifactStore, persist_run_artifact


ROOT = Path(__file__).resolve().parents[1]


def _runner_payload(status: str = "partial") -> dict:
    return {
        "schema_version": "1.4.0",
        "status": status,
        "reason": "hours_incomplete" if status != "ok" else "",
        "error_message": "stopped early" if status != "ok" else "",
        "run_metadata": {
            "started_at_utc": "2026-07-15T12:00:00Z",
            "feedstock_id": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
            "backend": "stub",
            "kernel_commit_sha": "abc123",
        },
        "per_hour_summary": [
            {
                "hour": 1,
                "campaign": "C0",
                "T_C": 900.0,
                "mass_balance_pct": 0.0,
                "metal_yields_kg": {},
                "O2_yield_kg_cumulative": 1.0,
            },
            {
                "hour": 2,
                "campaign": "C1",
                "T_C": 1400.0,
                "mass_balance_pct": 1e-13,
                "metal_yields_kg": {"Fe": 12.5},
                "O2_yield_kg_cumulative": 4.25,
            },
        ],
        "final_state": {"process.cleaned_melt": {"SiO2": 2.0}},
        "final": {"wall_deposit_by_species_kg": {}},
        "stage_purity_report": {"stage_1": {"verdict": "PURE"}},
        "vapor_pressure_source_report": {"status": "ok"},
    }


def test_build_run_artifact_repackages_runner_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "simulator.accounting.run_artifact.cache_version_for",
        lambda backend: f"{backend}-cache-v1",
    )
    payload = _runner_payload()
    artifact = build_run_artifact(payload, run_id="run-1", name="Lunar run")

    assert artifact["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert artifact["execution_status"] == "partial"
    assert artifact["lifecycle"] == "complete"
    assert artifact["failure"] == {
        "reason": "hours_incomplete",
        "error_message": "stopped early",
    }
    assert len(artifact["timesteps"]) == len(payload["per_hour_summary"])
    assert artifact["timesteps"][0]["summary"] is payload["per_hour_summary"][0]
    assert artifact["header"]["feedstock_id"] == "lunar_mare_low_ti"
    assert "seed" not in artifact["header"]
    assert "c3_dose" not in artifact["header"]
    assert "recipe_snapshot" not in artifact["header"]
    assert artifact["header"]["engine_identity"] == {
        "name": "stub",
        "cache_version": "stub-cache-v1",
        "backend_wire_token": "stub",
        "kernel_commit_sha": "abc123",
    }
    assert (
        artifact["header"]["engine_identity"]["cache_version"]
        != payload["run_metadata"]["kernel_commit_sha"]
    )
    assert artifact["terminal"]["final_state"] is payload["final_state"]
    assert artifact["terminal"]["mass_balance_closure"] == {
        "residual_pct": 1e-13,
        "basis": "final-hour percent",
    }
    assert "yield_disposition" not in artifact["terminal"]
    assert "wall_lifetime" not in artifact["terminal"]


@pytest.mark.parametrize("status", ["ok", "partial", "refused", "failed"])
def test_build_run_artifact_accepts_execution_status_enum(status: str) -> None:
    artifact = build_run_artifact(_runner_payload(status), run_id=f"run-{status}")

    assert artifact["execution_status"] == status
    assert ("failure" in artifact) is (status != "ok")


@pytest.mark.parametrize("status", [None, "complete", "OK", {}])
def test_build_run_artifact_rejects_unknown_execution_status(status) -> None:
    payload = _runner_payload()
    payload["status"] = status

    with pytest.raises(RunArtifactContractError, match="unknown execution status"):
        build_run_artifact(payload, run_id="run-invalid")


def test_build_run_artifact_rejects_missing_execution_status() -> None:
    payload = _runner_payload()
    del payload["status"]

    with pytest.raises(RunArtifactContractError, match="missing execution status"):
        build_run_artifact(payload, run_id="run-missing")


def test_build_run_artifact_captures_available_recipe_and_c3_dose() -> None:
    payload = _runner_payload("ok")
    payload["run_metadata"]["seed"] = 7
    payload["run_metadata"]["c3_alkali_credit_dose_kg_by_species"] = {
        "Na": 1.25,
        "K": 0.5,
    }
    payload["recipe_snapshot"] = {
        "setpoints_patch": {"campaigns": {"C1": {"target_C": 1400.0}}},
        "pins": ["campaigns.C1.target_C"],
        "recipe_schema_version": "recipe-schema-v1",
    }

    artifact = build_run_artifact(payload, run_id="run-recipe")

    assert artifact["header"]["seed"] == 7
    assert artifact["header"]["c3_dose"] == {"Na_kg": 1.25, "K_kg": 0.5}
    assert artifact["header"]["recipe_snapshot"] == payload["recipe_snapshot"]


def test_store_save_load_list_and_retention(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs", keep=1)
    first = build_run_artifact(_runner_payload(), run_id="run-1", name="First")
    first["header"]["created_at"] = "2026-07-14T12:00:00Z"
    first["header"]["starred"] = True
    second = build_run_artifact(_runner_payload("ok"), run_id="run-2", name="Second")
    third = build_run_artifact(_runner_payload("ok"), run_id="run-3", name="Third")
    third["header"]["created_at"] = "2026-07-16T12:00:00Z"

    store.save("run-1", first)
    with pytest.raises(FileExistsError):
        store.save("run-1", second)
    store.save("run-2", second)
    store.save("run-3", third)

    assert store.load("run-1") == first
    assert store.load("run-2") is None
    assert store.load("missing") is None
    summaries = store.list_runs()
    assert [summary["run_id"] for summary in summaries] == ["run-3", "run-1"]
    assert summaries[0]["peak_T_C"] == 1400.0
    assert summaries[0]["headline_yields_kg"] == {"Fe": 12.5, "O2": 4.25}
    assert summaries[1]["starred"] is True


def test_run_artifact_routes_return_index_full_artifact_and_404(tmp_path) -> None:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="run-artifact-test",
        RUN_ARTIFACT_DIR=str(tmp_path / "runs"),
    )
    app.register_blueprint(web_routes.bp)
    with app.app_context():
        artifact = persist_run_artifact(
            _runner_payload(), "run-1", name="Lunar run"
        )
    client = app.test_client()

    index_response = client.get("/api/runs")
    assert index_response.status_code == 200
    assert index_response.get_json()[0]["run_id"] == "run-1"
    artifact_response = client.get("/api/runs/run-1")
    assert artifact_response.status_code == 200
    assert artifact_response.get_json() == artifact
    assert client.get("/api/runs/missing").status_code == 404


def test_backfill_run_artifact_cli_round_trips_runner_payload(tmp_path) -> None:
    payload = _runner_payload("ok")
    payload_path = tmp_path / "runner-payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    runs_dir = tmp_path / "runs"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "backfill_run_artifact.py"),
            str(payload_path),
            "legacy-197h",
            "--name",
            "197h lunar",
            "--runs-dir",
            str(runs_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["stored_run_id"] == "legacy-197h"
    assert receipt["summary"]["name"] == "197h lunar"
    artifact = RunArtifactStore(runs_dir).load("legacy-197h")
    assert artifact is not None
    assert artifact["header"]["run_id"] == "legacy-197h"
    assert artifact["header"]["name"] == "197h lunar"
    assert artifact["execution_status"] == "ok"
    assert len(artifact["timesteps"]) == len(payload["per_hour_summary"])
    assert artifact["terminal"]["final_state"] == payload["final_state"]
