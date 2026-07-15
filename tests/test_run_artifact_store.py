from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
from web import run_store as run_store_module
from web.run_store import (
    InvalidRunIdError,
    RunArtifactStore,
    RunStoreCorruptionError,
    persist_run_artifact,
)


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
    second = build_run_artifact(_runner_payload("ok"), run_id="run-2", name="Second")
    third = build_run_artifact(_runner_payload("ok"), run_id="run-3", name="Third")
    third["header"]["created_at"] = "2026-07-16T12:00:00Z"

    store.save("run-1", first)
    store.update_meta("run-1", {"starred": True})
    assert store.save("run-1", second) is False
    store.save("run-2", second)
    store.save("run-3", third)

    assert store.load("run-1") == first
    assert store.load("run-2") is None
    assert store.load("missing") is None
    summaries = store.list_runs()
    assert [summary["run_id"] for summary in summaries] == ["run-3", "run-1"]
    assert summaries[0]["peak_T_C"] == 1400.0
    assert summaries[0]["headline_yields_kg"] == {"Fe": 12.5, "O2": 4.25}
    assert summaries[0]["headline_yield_semantics"] == {
        "Fe": "evolved_product",
        "O2": "source_side_potential",
    }
    assert summaries[0]["hours"] == 2
    assert summaries[0]["summary"] == "Fe 12.5 kg · O₂ (source-side) 4.25 kg"
    assert "folder" not in summaries[0]
    assert summaries[1]["starred"] is True


def test_store_meta_round_trip_is_idempotent_and_does_not_rewrite_artifact(
    tmp_path,
) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    artifact = build_run_artifact(_runner_payload("ok"), run_id="run-meta")
    assert store.save("run-meta", artifact) is True
    artifact_path = store.runs_dir / "run-meta.json"
    original_bytes = artifact_path.read_bytes()

    expected = {"starred": True, "folder": "Campaign A"}
    assert store.update_meta("run-meta", expected) == expected
    assert store.update_meta("run-meta", expected) == expected

    assert artifact_path.read_bytes() == original_bytes
    assert json.loads((store.runs_dir / "meta" / "run-meta.json").read_text()) == expected
    assert not list(store.runs_dir.rglob("*.tmp"))
    summary = store.list_runs()[0]
    assert summary["starred"] is True
    assert summary["folder"] == "Campaign A"

    assert store.update_meta("run-meta", {"starred": False, "folder": None}) == {
        "starred": False
    }
    summary = store.list_runs()[0]
    assert summary["starred"] is False
    assert "folder" not in summary


def test_store_rejects_dotted_id_and_keeps_meta_out_of_artifact_namespace(
    tmp_path,
) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    artifact = build_run_artifact(_runner_payload("ok"), run_id="foo")

    assert store.save("foo", artifact) is True
    assert store.update_meta("foo", {"starred": True}) == {"starred": True}
    with pytest.raises(InvalidRunIdError, match="run_id"):
        store.save("foo.meta", artifact)

    assert store.load("foo") == artifact
    assert json.loads((store.runs_dir / "meta" / "foo.json").read_text()) == {
        "starred": True
    }
    assert [path.name for path in store.runs_dir.glob("*.json")] == ["foo.json"]


def test_store_meta_atomic_failure_cleans_temp_and_preserves_previous_sidecar(
    tmp_path, monkeypatch
) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    artifact = build_run_artifact(_runner_payload("ok"), run_id="atomic")
    assert store.save("atomic", artifact) is True
    assert store.update_meta("atomic", {"starred": True}) == {"starred": True}
    meta_path = store.runs_dir / "meta" / "atomic.json"
    original_bytes = meta_path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(run_store_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        store.update_meta("atomic", {"folder": "Moon"})

    assert meta_path.read_bytes() == original_bytes
    assert not list(store.runs_dir.rglob("*.tmp"))


def test_store_corrupt_meta_quarantines_only_sidecar(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    artifact = build_run_artifact(_runner_payload("ok"), run_id="meta-corrupt")
    assert store.save("meta-corrupt", artifact) is True
    artifact_path = store.runs_dir / "meta-corrupt.json"
    meta_path = store.runs_dir / "meta" / "meta-corrupt.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text("{not-json", encoding="utf-8")

    summaries = store.list_runs()

    assert summaries[0]["run_id"] == "meta-corrupt"
    assert summaries[0]["starred"] is False
    assert artifact_path.exists()
    assert not meta_path.exists()
    assert (store.runs_dir / "meta" / "meta-corrupt.json.corrupt").exists()


def test_store_concurrent_meta_writers_leave_one_complete_valid_sidecar(
    tmp_path,
) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    artifact = build_run_artifact(_runner_payload("ok"), run_id="concurrent")
    assert store.save("concurrent", artifact) is True
    updates = [
        {"starred": bool(index % 2), "folder": f"folder-{index}"}
        for index in range(24)
    ]

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda update: store.update_meta("concurrent", update), updates))

    stored = json.loads(
        (store.runs_dir / "meta" / "concurrent.json").read_text(encoding="utf-8")
    )
    assert stored in updates
    assert not list(store.runs_dir.rglob("*.tmp"))


def test_store_parent_run_id_is_sidecar_only_and_absence_is_omitted(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    child = persist_run_artifact(
        _runner_payload("ok"),
        "child",
        store=store,
        parent_run_id="parent",
    )
    persist_run_artifact(_runner_payload("ok"), "independent", store=store)

    assert "parent_run_id" not in child["header"]
    assert store.load("child") == child
    summaries = {row["run_id"]: row for row in store.list_runs()}
    assert summaries["child"]["parent_run_id"] == "parent"
    assert "parent_run_id" not in summaries["independent"]


def test_store_lineage_failure_does_not_publish_artifact(tmp_path, monkeypatch) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    artifact = build_run_artifact(_runner_payload("ok"), run_id="child")
    original = store._save_parent_run_id
    attempts = 0

    def fail_once(run_id, parent_run_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("lineage write failed")
        original(run_id, parent_run_id)

    monkeypatch.setattr(store, "_save_parent_run_id", fail_once)
    with pytest.raises(OSError, match="lineage write failed"):
        store.save("child", artifact, parent_run_id="parent")

    assert not (store.runs_dir / "child.json").exists()
    assert store.save("child", artifact, parent_run_id="parent") is True
    assert store.load("child") == artifact
    assert json.loads((store.runs_dir / "meta" / "child.json").read_text()) == {
        "parent_run_id": "parent"
    }


def test_store_meta_update_cannot_succeed_after_retention_deletes_run(
    tmp_path, monkeypatch
) -> None:
    store = RunArtifactStore(tmp_path / "runs", keep=2)
    old = build_run_artifact(_runner_payload("ok"), run_id="old")
    old["header"]["created_at"] = "2026-07-14T12:00:00Z"
    assert store.save("old", old) is True
    store.keep = 1
    new = build_run_artifact(_runner_payload("ok"), run_id="new")
    new["header"]["created_at"] = "2026-07-16T12:00:00Z"
    retention_entered = run_store_module.threading.Event()
    release_retention = run_store_module.threading.Event()
    update_started = run_store_module.threading.Event()
    original_retention = store._apply_retention_locked

    def controlled_retention():
        retention_entered.set()
        assert release_retention.wait(timeout=5)
        original_retention()

    def star_old():
        update_started.set()
        return store.update_meta("old", {"starred": True})

    monkeypatch.setattr(store, "_apply_retention_locked", controlled_retention)
    with ThreadPoolExecutor(max_workers=2) as executor:
        save_future = executor.submit(store.save, "new", new)
        assert retention_entered.wait(timeout=5)
        star_future = executor.submit(star_old)
        assert update_started.wait(timeout=5)
        assert not star_future.done()
        release_retention.set()
        assert save_future.result(timeout=5) is True
        with pytest.raises(FileNotFoundError):
            star_future.result(timeout=5)

    assert store.load("old") is None


def test_store_corrupt_meta_is_quarantined_and_skipped_by_retention(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs", keep=2)
    protected = build_run_artifact(_runner_payload("ok"), run_id="protected")
    protected["header"]["created_at"] = "2026-07-14T12:00:00Z"
    assert store.save("protected", protected) is True
    meta_path = store.runs_dir / "meta" / "protected.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text("{not-json", encoding="utf-8")
    store.keep = 0
    trigger = build_run_artifact(_runner_payload("ok"), run_id="trigger")

    assert store.save("trigger", trigger) is True
    second_trigger = build_run_artifact(_runner_payload("ok"), run_id="trigger-2")
    assert store.save("trigger-2", second_trigger) is True

    assert store.load("protected") == protected
    assert not meta_path.exists()
    assert (store.runs_dir / "meta" / "protected.json.corrupt").exists()


def test_store_unstarred_run_reenters_keep_n_eviction(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs", keep=1)
    old = build_run_artifact(_runner_payload("ok"), run_id="old-star")
    old["header"]["created_at"] = "2026-07-14T12:00:00Z"
    assert store.save("old-star", old) is True
    store.update_meta("old-star", {"starred": True})
    middle = build_run_artifact(_runner_payload("ok"), run_id="middle")
    middle["header"]["created_at"] = "2026-07-15T12:00:00Z"
    assert store.save("middle", middle) is True
    store.update_meta("old-star", {"starred": False})
    newest = build_run_artifact(_runner_payload("ok"), run_id="newest")
    newest["header"]["created_at"] = "2026-07-16T12:00:00Z"

    assert store.save("newest", newest) is True

    assert store.load("old-star") is None
    assert store.load("middle") is None
    assert store.load("newest") == newest


def test_store_meta_path_uses_run_id_validation(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs")

    with pytest.raises(ValueError, match="run_id"):
        store.update_meta("../escape", {"starred": True})
    assert not (tmp_path / "runs" / "meta" / "escape.json").exists()


def test_store_corrupt_load_is_typed_and_list_quarantines(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    store.runs_dir.mkdir(parents=True)
    corrupt_path = store.runs_dir / "broken.json"
    corrupt_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RunStoreCorruptionError, match="corrupt run artifact"):
        store.load("broken")

    assert store.list_runs() == []
    assert not corrupt_path.exists()
    assert (store.runs_dir / "broken.json.corrupt").exists()


@pytest.mark.parametrize(
    "malformed_field, malformed_value",
    [("header", "not-an-object"), ("terminal", [])],
)
def test_store_malformed_nested_shape_is_typed_and_quarantined(
    tmp_path, malformed_field, malformed_value
) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    store.runs_dir.mkdir(parents=True)
    artifact = build_run_artifact(_runner_payload("ok"), run_id="malformed")
    artifact[malformed_field] = malformed_value
    malformed_path = store.runs_dir / "malformed.json"
    malformed_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(RunStoreCorruptionError, match=f"expected {malformed_field}"):
        store.load("malformed")

    assert store.list_runs() == []
    assert not malformed_path.exists()
    assert (store.runs_dir / "malformed.json.corrupt").exists()


@pytest.mark.parametrize(
    "strip_key, match",
    [
        ("header", "expected header"),
        ("terminal", "expected terminal"),
        ("timesteps", "missing timesteps"),
    ],
)
def test_store_missing_structural_key_is_typed_and_quarantined(
    tmp_path, strip_key, match
) -> None:
    # Structural keys are required, not merely well-typed-when-present: an
    # artifact missing header/terminal/timesteps must be quarantined here,
    # not passed through to crash readers downstream.
    store = RunArtifactStore(tmp_path / "runs")
    store.runs_dir.mkdir(parents=True)
    artifact = build_run_artifact(_runner_payload("ok"), run_id="stripped")
    del artifact[strip_key]
    stripped_path = store.runs_dir / "stripped.json"
    stripped_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(RunStoreCorruptionError, match=match):
        store.load("stripped")

    assert store.list_runs() == []
    assert not stripped_path.exists()
    assert (store.runs_dir / "stripped.json.corrupt").exists()


def test_store_timestep_without_summary_is_typed_and_quarantined(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    store.runs_dir.mkdir(parents=True)
    artifact = build_run_artifact(_runner_payload("ok"), run_id="no-summary")
    artifact["timesteps"].append({"hour": 999})
    bad_path = store.runs_dir / "no-summary.json"
    bad_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(RunStoreCorruptionError, match="summary"):
        store.load("no-summary")

    assert store.list_runs() == []
    assert (store.runs_dir / "no-summary.json.corrupt").exists()


def test_store_summary_omits_absent_species_and_labels_source_side_o2(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    artifact = build_run_artifact(_runner_payload("ok"), run_id="o2-only")
    artifact["timesteps"][-1]["summary"]["metal_yields_kg"] = {}

    assert store.save("o2-only", artifact) is True

    summary = store.list_runs()[0]
    assert summary["summary"] == "O₂ (source-side) 4.25 kg"
    assert "Fe" not in summary["summary"]
    assert summary["headline_yields_kg"] == {"O2": 4.25}

    fe_only_store = RunArtifactStore(tmp_path / "fe-only-runs")
    fe_only = build_run_artifact(_runner_payload("ok"), run_id="fe-only")
    final_summary = fe_only["timesteps"][-1]["summary"]
    final_summary.pop("O2_yield_kg_cumulative")
    final_summary.pop("O2_source_side_potential_kg_cumulative", None)
    assert fe_only_store.save("fe-only", fe_only) is True

    fe_only_summary = fe_only_store.list_runs()[0]
    assert fe_only_summary["summary"] == "Fe 12.5 kg"
    assert "O₂" not in fe_only_summary["summary"]
    assert fe_only_summary["headline_yields_kg"] == {"Fe": 12.5}

    empty_store = RunArtifactStore(tmp_path / "empty-runs")
    empty = build_run_artifact(_runner_payload("refused"), run_id="empty")
    empty["timesteps"] = []
    assert empty_store.save("empty", empty) is True
    empty_summary = empty_store.list_runs()[0]
    assert empty_summary["headline_yields_kg"] == {}
    assert "headline_yield_semantics" not in empty_summary
    assert "hours" not in empty_summary


def test_store_stale_lock_file_does_not_block_retry(tmp_path) -> None:
    store = RunArtifactStore(tmp_path / "runs")
    store.runs_dir.mkdir(parents=True)
    (store.runs_dir / "run-retry.write-lock").write_text(
        "stale writer metadata",
        encoding="utf-8",
    )
    artifact = build_run_artifact(
        _runner_payload("failed"),
        run_id="run-retry",
    )

    assert store.save("run-retry", artifact) is True
    assert store.load("run-retry") == artifact


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

    corrupt_path = tmp_path / "runs" / "corrupt.json"
    corrupt_path.write_text("{not-json", encoding="utf-8")
    corrupt_response = client.get("/api/runs/corrupt")
    assert corrupt_response.status_code == 500
    assert corrupt_response.get_json()["error_type"] == "run_store_corruption"


def test_run_meta_route_round_trip_validation_and_404(tmp_path) -> None:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="run-meta-test",
        RUN_ARTIFACT_DIR=str(tmp_path / "runs"),
    )
    app.register_blueprint(web_routes.bp)
    with app.app_context():
        persist_run_artifact(_runner_payload("ok"), "run-meta")
    client = app.test_client()

    response = client.patch(
        "/api/runs/run-meta/meta",
        json={"starred": True, "folder": "Favorites"},
    )
    assert response.status_code == 200
    assert response.get_json() == {
        "run_id": "run-meta",
        "starred": True,
        "folder": "Favorites",
    }
    assert client.patch(
        "/api/runs/run-meta/meta",
        json={"starred": True, "folder": "Favorites"},
    ).get_json() == response.get_json()
    index_row = client.get("/api/runs").get_json()[0]
    assert index_row["starred"] is True
    assert index_row["folder"] == "Favorites"

    unknown = client.patch(
        "/api/runs/run-meta/meta", json={"arbitrary": "rejected"}
    )
    assert unknown.status_code == 400
    assert unknown.get_json()["error_type"] == "invalid_run_metadata"
    assert "unknown run metadata keys" in unknown.get_json()["error"]

    malformed = client.patch(
        "/api/runs/run-meta/meta",
        data="[]",
        content_type="application/json",
    )
    assert malformed.status_code == 400
    assert malformed.get_json()["error_type"] == "invalid_run_metadata"

    missing = client.patch("/api/runs/missing/meta", json={"starred": True})
    assert missing.status_code == 404
    assert missing.get_json()["error_type"] == "run_not_found"


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

    duplicate = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "backfill_run_artifact.py"),
            str(payload_path),
            "legacy-197h",
            "--runs-dir",
            str(runs_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert duplicate.returncode == 2
    assert duplicate.stdout == ""
    assert "duplicate" in duplicate.stderr
    assert "nothing stored" in duplicate.stderr
