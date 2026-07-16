from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
import yaml
from flask import Flask

from simulator.recipe_io import normalize_recipe_patch
from web.routes import bp
from web.run_store import RunArtifactStore


def _app(tmp_path: Path) -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        RUN_ARTIFACT_DIR=tmp_path / "runs",
    )
    app.register_blueprint(bp)
    return app


def _artifact(*, recipe_snapshot: dict | None) -> dict:
    header = {
        "run_id": "viewer-export",
        "feedstock_id": "lunar_mare_low_ti",
        "charge_mass_kg": 125.0,
        "seed": 17,
    }
    if recipe_snapshot is not None:
        header["recipe_snapshot"] = recipe_snapshot
    return {
        "artifact_schema_version": "0.1.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": header,
        "timesteps": [],
        "terminal": {},
    }


def test_report_viewer_serves_index_and_assets(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    index = client.get("/report/")
    script = client.get("/report/settings.js")

    assert index.status_code == 200
    assert b"Regolith Refinery Run Report" in index.data
    assert script.status_code == 200
    assert b"Download run.yaml" in script.data


@pytest.mark.parametrize("include_activity", [False, True])
def test_report_viewer_presence_gates_stage_purity_activity(
    include_activity: bool,
) -> None:
    script_path = Path(__file__).resolve().parents[1] / "web/report_viewer/report-viewer.js"
    stage = {
        "label": "Cr <stage>",
        "accepted_species": ["Cr<script>", "Mn"],
        "total_kg": 1.0,
        "designated_kg": 1.0,
        "impurity_kg": 0.0,
        "purity_fraction": 1.0,
        "verdict": "PURE",
    }
    if include_activity:
        stage["activity"] = {"Cr<script>": True, "Mn": False}
    artifact = {
        "artifact_schema_version": "0.1.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "purity-activity"},
        "timesteps": [],
        "terminal": {"stage_purity": {"stage_2": stage}},
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const report = { innerHTML: "" };
const context = {
  window: { location: { search: "" } },
  document: { querySelector: (selector) => selector === "#report" ? report : null },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[3]) })
};
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""

    completed = subprocess.run(
        ["node", "-", str(script_path), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "<script>" not in completed.stdout
    assert "Cr&lt;script&gt;" in completed.stdout
    if include_activity:
        assert "Cr&lt;script&gt; · ACTIVE" in completed.stdout
        assert "Mn · IDLE" in completed.stdout
        assert "Pending W-A10" not in completed.stdout
    else:
        assert "Cr&lt;script&gt; · Mn" in completed.stdout
        assert "ACTIVE" not in completed.stdout
        assert "IDLE" not in completed.stdout
        assert "Pending W-A10" in completed.stdout


@pytest.mark.parametrize("has_recipe_snapshot", [True, False])
def test_settings_script_executes_live_run_resolution_and_manifest_gate(
    has_recipe_snapshot: bool,
) -> None:
    script_path = Path(__file__).resolve().parents[1] / "web/report_viewer/settings.js"
    artifact = _artifact(
        recipe_snapshot=(
            {
                "setpoints_patch": {},
                "pins": [],
                "recipe_schema_version": "recipe-schema-v1",
            }
            if has_recipe_snapshot
            else None
        )
    )
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(process.argv[2], "utf8");
const settings = { innerHTML: "" };
const download = { addEventListener() {} };
let fetched = null;
const context = {
  window: { location: { search: process.argv[3] } },
  document: {
    querySelector(selector) {
      if (selector === "#settings") return settings;
      if (selector === "#download-run") return download;
      throw new Error(`unexpected selector ${selector}`);
    },
    createElement() { throw new Error("download should not execute during render"); }
  },
  URLSearchParams,
  Blob,
  URL,
  encodeURIComponent,
  setTimeout,
  fetch: async (url) => {
    fetched = url;
    return { ok: true, json: async () => JSON.parse(process.argv[4]) };
  }
};
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(JSON.stringify({ fetched, html: settings.innerHTML })));
"""

    completed = subprocess.run(
        ["node", "-", str(script_path), "?run=run/live", json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)

    assert result["fetched"] == "/api/runs/run%2Flive"
    assert './index.html?run=run%2Flive' in result["html"]
    if has_recipe_snapshot:
        assert '/api/runs/run%2Flive/run.yaml' in result["html"]
        assert "Download run.yaml unavailable" not in result["html"]
    else:
        assert '/api/runs/run%2Flive/run.yaml' not in result["html"]
        assert "Download run.yaml unavailable" in result["html"]


def test_report_viewer_serves_only_viewer_asset_types(tmp_path: Path) -> None:
    # send_from_directory alone would publish EVERY regular file in the
    # source dir — non-asset files (freeze_sample.py) and dotfiles must 404.
    client = _app(tmp_path).test_client()

    assert client.get("/report/freeze_sample.py").status_code == 404
    assert client.get("/report/.hidden.json").status_code == 404
    assert client.get("/report/sample-run-artifact.json").status_code == 200
    assert client.get("/report/library.html").status_code == 200


def test_report_viewer_rejects_path_traversal(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    assert client.get("/report/../routes.py").status_code == 404
    assert client.get("/report/%2e%2e/routes.py").status_code == 404


def test_report_viewer_rejects_untrusted_host(tmp_path: Path) -> None:
    response = _app(tmp_path).test_client().get(
        "/report/",
        headers={"Host": "attacker.example:3000"},
    )

    assert response.status_code == 403
    assert response.get_json() == {
        "error": "request Host does not match the configured server bind",
        "error_type": "untrusted_request_host",
    }


def test_run_manifest_round_trips_stored_recipe_snapshot(tmp_path: Path) -> None:
    app = _app(tmp_path)
    snapshot = {
        "setpoints_patch": {
            "campaigns": {"C4": {"temp_range_C": [1600.0, 1660.0]}},
        },
        "pins": ["campaigns.C4.temp_range_C"],
        "recipe_schema_version": "recipe-schema-v1",
    }
    RunArtifactStore(tmp_path / "runs").save(
        "viewer-export",
        _artifact(recipe_snapshot=snapshot),
    )

    response = app.test_client().get("/api/runs/viewer-export/run.yaml")

    assert response.status_code == 200
    assert response.content_type == "application/yaml; charset=utf-8"
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="run-viewer-export.yaml"'
    )
    manifest = yaml.safe_load(response.data)
    assert manifest == {
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 125.0,
        "seed": 17,
        **snapshot,
    }
    assert normalize_recipe_patch(
        manifest["setpoints_patch"],
        source="exported run manifest",
    ) == snapshot["setpoints_patch"]


def test_run_manifest_without_recipe_snapshot_returns_typed_error(tmp_path: Path) -> None:
    app = _app(tmp_path)
    RunArtifactStore(tmp_path / "runs").save(
        "viewer-export",
        _artifact(recipe_snapshot=None),
    )

    response = app.test_client().get("/api/runs/viewer-export/run.yaml")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "artifact carries no recipe snapshot; export unavailable",
        "error_type": "run_manifest_unavailable",
    }
