from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess

import pytest
import yaml
from flask import Flask

from simulator.accounting.run_artifact import ARTIFACT_SCHEMA_VERSION
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
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": header,
        "timesteps": [],
        "terminal": {},
    }


def _run_viewer_expression(script_name: str, expression: str):
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = {
  window: { location: { search: "", href: "" } },
  document: { title: "", querySelector() { return null; }, querySelectorAll() { return []; } },
  URLSearchParams,
  encodeURIComponent,
  fetch: () => new Promise(() => {}),
  console,
  setTimeout
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
process.stdout.write(JSON.stringify(vm.runInContext(process.argv[4], context)));
"""
    completed = subprocess.run(
        ["node", "-", str(root / "labels.js"), str(root / script_name), expression],
        input=harness, text=True, capture_output=True, check=True,
    )
    return json.loads(completed.stdout)


def test_report_viewer_serves_index_and_assets(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    index = client.get("/report/")
    script = client.get("/report/settings.js")

    assert index.status_code == 200
    assert b"Regolith Refinery Run Report" in index.data
    assert script.status_code == 200
    assert b"Download run.yaml" in script.data


def test_report_viewer_reads_canonical_cost_provenance_key() -> None:
    root = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
    report_source = (root / "report-viewer.js").read_text(encoding="utf-8")
    settings_source = (root / "settings.js").read_text(encoding="utf-8")
    sample = json.loads((root / "sample-run-artifact.json").read_text(encoding="utf-8"))

    assert "cost_block?.provenance" in report_source
    assert "prices.provenance" in report_source
    assert "cost.provenance" in settings_source
    assert sample["header"]["cost_block"]["provenance"]
    assert "_provenance" not in sample["header"]["cost_block"]


def test_report_viewer_kg_energy_paths_reject_non_numeric_coercion() -> None:
    """Regression (O1 / L3-F2): booleans/arrays/blank strings in kg/energy fields must render honest
    'not emitted', never a coerced number. The old `Number.isFinite(Number(v))` gate let true→1, false→0,
    []→0 through kg/energy/sum paths (the kg twin of the mol path's strictMol guard)."""
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    artifact = {
        "artifact_schema_version": "0.2.0",
        "header": {"run_id": "poison-run", "name": "poison", "feedstock_id": "lunar_mare_low_ti"},
        "timesteps": [
            {
                "hour": 1,
                "summary": {
                    "campaign": "C0",
                    "metal_yields_kg": {"Fe": True},
                    "O2_source_side_potential_kg_cumulative": False,
                    "energy_electrical_kWh": True,
                    "energy_evaporation_thermal_kWh": [],
                },
                "ledger": {"process.cleaned_melt": {"SiO2": 10.0}},
            },
        ],
        "terminal": {"final_state": {"process.cleaned_melt": {"SiO2": 10.0}}, "stage_purity": {}},
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const els = {};
function mockEl() {
  return { _html: "", value: "", disabled: false, textContent: "",
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = v; },
    addEventListener() {}, setAttribute() {}, removeAttribute() {}, focus() {},
    classList: { add() {}, remove() {}, toggle() {} }, dataset: {} };
}
const context = {
  window: { location: { search: "" } },
  document: { title: "",
    querySelector: (s) => (els[s] = els[s] || mockEl()),
    querySelectorAll: () => [] },
  URLSearchParams, encodeURIComponent, setTimeout,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
context.globalThis = context;
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(reportSource, context);
setImmediate(() => process.stdout.write(Object.values(els).map((e) => e._html).join("\n")));
"""
    completed = subprocess.run(
        ["node", "-", str(root / "labels.js"), str(root / "report-viewer.js"), json.dumps(artifact)],
        input=harness, text=True, capture_output=True, check=True,
    )
    html = completed.stdout
    assert "Report unavailable" not in html
    assert "1 kg" not in html
    assert "0 kg" not in html
    assert "1 kWh" not in html and "1.0 kWh" not in html
    assert "not emitted" in html


def test_report_viewer_c3_dose_rejects_string_numeric_coercion() -> None:
    html = _run_viewer_expression(
        "report-viewer.js",
        "makeHeader({header:{c3_dose:{Fe_kg:'12'}}},[{}],{})",
    )

    assert "Fe 12 kg" not in html
    assert "Fe not emitted" in html


def test_report_viewer_c3_dose_preserves_tiny_finite_value() -> None:
    html = _run_viewer_expression(
        "report-viewer.js",
        "makeHeader({header:{c3_dose:{Fe_kg:0.000128}}},[{}],{})",
    )

    assert "0.000128 kg" in html
    assert 'title="0.000128 kg"' in html


def test_report_viewer_scalar_labels_do_not_render_objects() -> None:
    html = _run_viewer_expression(
        "report-viewer.js",
        "makeHeader({header:{campaign_chain:[{}]},failure:{reason:{}},execution_status:'failed'},[{}],{}) + tapsAndPuritySection({stage_purity:{stage1:{accepted_species:[{}],total_kg:1}}})",
    )

    assert "[object Object]" not in html
    assert "malformed (object)" in html


def test_report_viewer_does_not_derive_account_totals_or_mol_percentages() -> None:
    html = _run_viewer_expression(
        "report-viewer.js",
        "ledgerSection({feed:{Fe:2,O2:3},empty:{}}) + ceramicSection({final_state:{'process.cleaned_melt':{FeO:1,SiO2:3}}})",
    )

    assert ">5 mol<" not in html and ">0 mol<" not in html
    assert "not emitted" in html
    assert "75.0000%" not in html


def test_report_viewer_headline_cost_marks_viewer_estimate() -> None:
    html = _run_viewer_expression(
        "report-viewer.js",
        "makeHeader({header:{cost_block:{},run_id:'r'}},[],{electrical:1,thermal:2,totalCost:14,canonicalCostTotals:false})",
    )

    assert "viewer-computed estimate (no emitted cost total)" in html


def test_report_viewer_sparse_stage_purity_stays_pending() -> None:
    html = _run_viewer_expression(
        "report-viewer.js",
        "tapsAndPuritySection({stage_purity:{stage_0:{verdict:'PURE',purity_fraction:1}}})",
    )

    assert "PENDING" in html
    assert "100.0000%" not in html


def test_report_viewer_zero_step_inventory_is_not_terminal_ceramic() -> None:
    html = _run_viewer_expression(
        "report-viewer.js",
        "ceramicSection({final_state:{'process.cleaned_melt':{FeO:1}},terminal_product_taxonomy:null},false)",
    )

    assert "Initial or unprocessed inventory is not presented as terminal product" in html
    assert "<h2><span class=\"sect\">07</span>Cleaned-melt inventory" in html


def test_report_viewer_money_preserves_nonzero_subcent_cost() -> None:
    rendered = _run_viewer_expression("report-viewer.js", "money(0.000128)")

    assert "&lt; $0.01" in rendered
    assert "0.000128 USD" in rendered


def test_library_and_settings_reject_numeric_coercion() -> None:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
function run(sourcePath, expression) {
  const context = {
    window: { location: { search: "", href: "" } },
    document: { querySelector() { return null; } },
    URLSearchParams,
    encodeURIComponent,
    fetch: () => new Promise(() => {}),
    console
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(labelsSource, context);
  vm.runInContext(fs.readFileSync(sourcePath, "utf8"), context);
  return vm.runInContext(expression, context);
}
process.stdout.write(JSON.stringify({
  library: run(process.argv[3], '[exactNumber([], "kg"), exactNumber("  ", "kg"), exactNumber("12", "kg")]'),
  settings: run(process.argv[4], '[displayNumber([], "kg"), displayNumber("  ", "kg"), displayNumber("12", "kg")]')
}));
"""
    completed = subprocess.run(
        [
            "node", "-", str(root / "labels.js"),
            str(root / "library.js"), str(root / "settings.js"),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)

    assert result["library"] == ["not emitted"] * 3
    assert result["settings"] == ["not emitted"] * 3


def test_shared_escape_marks_non_scalars_across_viewer_modules() -> None:
    values = {
        script: _run_viewer_expression(
            script,
            '[ReportLabels.esc({bad: 1}), esc({bad: 1}), esc([1, 2])]',
        )
        for script in ("report-viewer.js", "library.js", "settings.js")
    }

    for escaped in values.values():
        assert escaped == ["malformed (object)", "malformed (object)", "malformed (array)"]


def _render_library_yield_chips(payload: dict) -> str:
    script_path = Path(__file__).resolve().parents[1] / "web/report_viewer/library.js"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const context = {
  document: { querySelector() { return null; } },
  fetch() { return new Promise(() => {}); }
};
vm.createContext(context);
vm.runInContext(labelsSource, context);
vm.runInContext(source, context);
process.stdout.write(context.yieldChips(JSON.parse(process.argv[4])));
"""
    completed = subprocess.run(
        ["node", "-", str(script_path.parent / "labels.js"), str(script_path), json.dumps(payload)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def test_library_renders_canonical_headline_o2() -> None:
    html = _render_library_yield_chips({"headline_yields_kg": {"O2": 4.25}})

    assert "O₂ source-side potential (not recovered)" in html
    assert 'title="4.25 kg"' in html


def test_library_spurious_top_level_o2_alias_does_not_override_canonical() -> None:
    html = _render_library_yield_chips(
        {
            "headline_yields_kg": {"O2": 4.25},
            "O2_source_side_potential_kg_cumulative": 99,
            "O2_metric_label": "spoofed label",
        }
    )

    assert "O₂ source-side potential (not recovered)" in html
    assert 'title="4.25 kg"' in html
    assert "99 kg" not in html
    assert "spoofed label" not in html


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
const labels = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const context = {
  window: { location: { search: "" } },
  document: { querySelector: (selector) => selector === "#report" ? report : null },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
vm.runInNewContext(labels, context);
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""

    completed = subprocess.run(
        ["node", "-", str(script_path.parent / "labels.js"), str(script_path), json.dumps(artifact)],
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


def test_report_viewer_empty_stage_carries_indeterminate_token() -> None:
    script_path = Path(__file__).resolve().parents[1] / "web/report_viewer/report-viewer.js"
    artifact = {
        "artifact_schema_version": "0.1.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "empty-purity"},
        "timesteps": [],
        "terminal": {
            "stage_purity": {
                "stage_0": {
                    "label": "Hot Duct",
                    "accepted_species": [],
                    "total_kg": 0.0,
                    "designated_kg": 0.0,
                    "impurity_kg": 0.0,
                    "purity_fraction": None,
                    "verdict": "INDETERMINATE",
                    "reason": "no_captured_mass",
                }
            }
        },
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labels = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const context = {
  window: { location: { search: "" } },
  document: { querySelector: (selector) => selector === "#report" ? report : null },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
vm.runInNewContext(labels, context);
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""
    completed = subprocess.run(
        ["node", "-", str(script_path.parent / "labels.js"), str(script_path), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    html = completed.stdout
    assert "INDETERMINATE" in html
    assert "PURE" not in html
    assert "no material" in html
    assert "100.0000%" not in html
    assert "0.0000%" not in html


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
const labels = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const settings = { innerHTML: "" };
const download = { addEventListener() {} };
let fetched = null;
const context = {
  window: { location: { search: process.argv[4] } },
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
    return { ok: true, json: async () => JSON.parse(process.argv[5]) };
  }
};
vm.runInNewContext(labels, context);
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(JSON.stringify({ fetched, html: settings.innerHTML })));
"""

    completed = subprocess.run(
        ["node", "-", str(script_path.parent / "labels.js"), str(script_path), "?run=run/live", json.dumps(artifact)],
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


def _render_report_state_with_panels(artifact: dict | Path, panels_js: str = "") -> dict:
    """Render the report with panel modules registered on globalThis.ReportPanels."""
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const artifactArg = process.argv[4];
const artifact = JSON.parse(artifactArg.startsWith("{") ? artifactArg : fs.readFileSync(artifactArg, "utf8"));
const calls = { render: [], scrub: [], errors: [] };
const nodes = new Map();
function el(id) {
  if (!nodes.has(id)) {
    nodes.set(id, {
      id, textContent: "", value: "", disabled: false, events: {}, markup: "",
      set innerHTML(value) {
        this.markup = value;
        for (const match of value.matchAll(/\bid="([^"]+)"/g)) el(match[1]);
      },
      get innerHTML() { return this.markup; },
      attributes: {},
      setAttribute(name, value) { this.attributes[name] = String(value); },
      getAttribute(name) { return this.attributes[name] ?? null; },
      addEventListener(type, callback) { this.events[type] = callback; }, focus() {},
      insertBefore(element, reference) {
        element.parentElement = this;
        element.nextElementSibling = reference;
        reference.previousElementSibling = element;
      },
      classList: { add() {}, remove() {}, toggle() {} }
    });
  }
  return nodes.get(id);
}
const report = el("report");
const context = {
  window: { location: { search: "" } },
  document: {
    title: "",
    querySelector(selector) {
      if (selector === "#report") return report;
      if (selector.startsWith("#")) return nodes.get(selector.slice(1)) || null;
      if (selector === ".stepper" && nodes.has("stepper")) {
        const stepper = el("stepper-root");
        el("current-grid").parentElement = stepper;
        return stepper;
      }
      if (selector === ".status-pill" && nodes.has("stepper")) return el("status-pill");
      return null;
    },
    getElementById(id) { return nodes.get(id) || null; },
    querySelectorAll() { return []; }
  },
  URLSearchParams,
  encodeURIComponent,
  setTimeout,
  console: { error(...args) { calls.errors.push(args.map(String).join(" ")); }, warn() {}, log() {} },
  fetch: async () => ({ ok: true, json: async () => artifact })
};
context.globalThis = context;
vm.runInNewContext(labelsSource, context);
const root = require("path").dirname(process.argv[2]);
const index = fs.readFileSync(require("path").join(root, "index.html"), "utf8");
const scripts = [...index.matchAll(/<script src="\.\/([^"]+)" defer><\/script>/g)].map((match) => match[1]);
for (const script of scripts.filter((name) => name.startsWith("panels/"))) {
  vm.runInNewContext(fs.readFileSync(require("path").join(root, script), "utf8"), context);
}
vm.runInNewContext(process.argv[5], context);
for (const panel of context.ReportPanels || []) {
  if (!panel || typeof panel !== "object") continue;
  if (typeof panel.render === "function") {
    const original = panel.render;
    panel.render = (...args) => {
      calls.render.push({ id: panel.id, summaries: args[1].every((row, i) => row === artifact.timesteps[i].summary) });
      return original(...args);
    };
  }
  if (typeof panel.onTimestep === "function") {
    const original = panel.onTimestep;
    panel.onTimestep = (...args) => { calls.scrub.push([panel.id, args[1]]); return original(...args); };
  }
}
vm.runInNewContext(reportSource, context);
setImmediate(() => {
  const snapshots = [];
  const snapshot = () => snapshots.push(Object.fromEntries([...nodes].map(([id, node]) => [id, node.innerHTML || node.textContent])));
  snapshot();
  if (nodes.has("stepper")) {
    for (const index of [artifact.timesteps.length - 1, 0]) {
      const slider = el("stepper");
      slider.value = String(index);
      slider.events.input();
      snapshot();
    }
  }
  const p13 = nodes.get("sec-p13-status-strip");
  process.stdout.write(JSON.stringify({
    html: report.innerHTML, calls, scripts, snapshots,
    pinned: !!p13 && p13.parentElement === nodes.get("stepper-root") && p13.nextElementSibling === nodes.get("current-grid")
  }));
});
"""
    completed = subprocess.run(
        ["node", "-", str(root / "labels.js"), str(root / "report-viewer.js"),
         str(artifact) if isinstance(artifact, Path) else json.dumps(artifact), panels_js],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _render_report_html_with_panels(artifact: dict, panels_js: str) -> str:
    return _render_report_state_with_panels(artifact, panels_js)["html"]


def test_report_viewer_renders_malformed_timestep_scalars_honestly() -> None:
    artifact = {
        "artifact_schema_version": "0.2.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "malformed-scalars", "name": "test", "feedstock_id": "test"},
        "timesteps": [
            {"hour": {"unexpected": 1}, "summary": {"campaign": {"unexpected": 2}}},
        ],
        "terminal": {},
    }

    state = _render_report_state_with_panels(artifact)

    assert "[object Object]" not in state["html"]
    assert "malformed (object)" in state["html"]
    assert state["snapshots"][0]["step-output"] == "Hour malformed (object) · malformed (object)"


def test_report_viewer_uses_legacy_source_side_o2_alias_with_metric_label() -> None:
    artifact = {
        "artifact_schema_version": "0.2.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "legacy-o2", "feedstock_id": "lunar_mare_low_ti", "name": "legacy"},
        "timesteps": [
            {
                "hour": 1,
                "summary": {
                    "campaign": "C0",
                    "O2_yield_kg_cumulative": 4.25,
                    "O2_metric_label": "source-side O2 potential (emitted; not recovered)",
                },
                "ledger": {},
            }
        ],
        "terminal": {},
    }

    state = _render_report_state_with_panels(artifact)
    fallback_cases = _run_viewer_expression(
        "report-viewer.js",
        "[sourceSideO2({O2_source_side_potential_kg_cumulative: 1.5, O2_yield_kg_cumulative: 2.5}), "
        "sourceSideO2({O2_source_side_potential_kg_cumulative: ' ', O2_yield_kg_cumulative: 2.5}), "
        "sourceSideO2({O2_source_side_potential_kg_cumulative: false, O2_yield_kg_cumulative: []})]",
    )

    assert state["html"].count("4.25 kg") >= 2
    assert "source-side O2 potential (emitted; not recovered)" in state["html"]
    assert "O2_yield_kg_cumulative" in state["html"]
    assert fallback_cases == [1.5, 2.5, None]


def test_report_viewer_equipment_diagram_uses_canonical_source_side_o2() -> None:
    def p15_source_o2(summary: dict) -> str:
        artifact = _artifact(recipe_snapshot=None)
        artifact["timesteps"] = [{"hour": 1, "summary": {"campaign": "C0", **summary}, "ledger": {}}]
        html = _render_report_state_with_panels(artifact)["html"]
        return re.search(r'<div class="sec-p15-source-o2[^>]*>.*?</div>', html).group(0)

    canonical = p15_source_o2({"O2_source_side_potential_kg_cumulative": 4.25})
    legacy = p15_source_o2({"O2_yield_kg_cumulative": 3.5})
    absent = p15_source_o2({})

    assert "4.25 kg" in canonical and "not emitted" not in canonical
    assert "3.5 kg" in legacy and "O2_yield_kg_cumulative" in legacy
    assert "not emitted" in absent


def test_report_viewer_labels_viewer_derived_projections() -> None:
    artifact = _artifact(recipe_snapshot=None)
    artifact["timesteps"] = [
        {"hour": 1, "summary": {"campaign": "C0", "T_C": 100.0, "energy_electrical_kWh": 1.0, "energy_evaporation_thermal_kWh": 2.0}, "ledger": {}},
        {"hour": 2, "summary": {"campaign": "C0", "T_C": 200.0, "energy_electrical_kWh": 3.0, "energy_evaporation_thermal_kWh": 4.0}, "ledger": {}},
    ]

    html = _render_report_state_with_panels(artifact)["html"]

    assert "viewer-derived peak" in html
    assert "viewer-derived timestep rows" in html
    assert "Viewer-derived temperature range" in html
    assert "Viewer-derived electrical + evaporation thermal" in html
    assert "Fe evolved" not in html


def test_report_viewer_accepted_species_scalar_text_is_escaped() -> None:
    html = _run_viewer_expression(
        "report-viewer.js",
        "tapsAndPuritySection({stage_purity:{stage1:{accepted_species:['<img src=x onerror=alert(1)>'],total_kg:1}}})",
    )

    assert "&lt;img" in html
    assert "<img src=x" not in html


def test_wall_deposit_empty_segment_stays_pending() -> None:
    artifact = _artifact(recipe_snapshot=None)
    artifact["timesteps"] = [{"hour": 1, "summary": {"campaign": "C0", "wall_deposit_cumulative_kg": {"stage_0_to_1": {}}}, "ledger": {}}]

    html = _render_report_state_with_panels(artifact)["html"]

    assert "none emitted" in html
    assert "viewer-side sum (no emitted total)" not in html
    assert ">0 kg</span>" not in html


def test_wall_deposit_malformed_segment_stays_pending_but_keeps_valid_evidence() -> None:
    artifact = _artifact(recipe_snapshot=None)
    artifact["timesteps"] = [{"hour": 1, "summary": {"campaign": "C0", "wall_deposit_cumulative_kg": {"bad": [1, 2], "good": {"Fe": 2.0}}}, "ledger": {}}]

    html = _render_report_state_with_panels(artifact)["html"]

    assert "viewer-side sum (no emitted total)" not in html
    assert "good" in html and "Fe" in html
    assert "3 kg" not in html


def test_incomplete_cost_inputs_stay_pending_without_estimate_label() -> None:
    artifact = _artifact(recipe_snapshot=None)
    artifact["header"]["cost_block"] = {"electrical_cost_per_kWh": 10.0}
    artifact["timesteps"] = [{"hour": 1, "summary": {"campaign": "C0", "energy_electrical_kWh": 1.0, "energy_evaporation_thermal_kWh": 2.0}, "ledger": {}}]

    html = _render_report_state_with_panels(artifact)["html"]

    assert "Pending cost estimate" in html
    assert "viewer-computed estimate" not in html
    assert "Total not emitted" not in html


def test_report_viewer_no_rows_pumping_is_pending_not_measured_zero() -> None:
    artifact = _artifact(recipe_snapshot=None)
    artifact["timesteps"] = []
    artifact["terminal"]["run_metadata"] = {
        "cost_rollup_diagnostic": {
            "pumping_diagnostic": {
                "status": "no_rows",
                "rows": [],
                "pumping_electrical_kWh": 0.0,
            }
        }
    }

    html = _render_report_state_with_panels(artifact)["html"]

    assert "Pumping energy</span><b>not computed — no rows" in html
    assert "Pumping status</span><b>no_rows" in html
    assert 'Pumping energy</span><b><span title="0 kWh">0 kWh' not in html
    assert "pumping_diagnostic.pumping_electrical_kWh not emitted" in html


def test_library_pretty_prints_feedstock_ids() -> None:
    root = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const librarySource = fs.readFileSync(process.argv[3], "utf8");
const context = {
  window: { location: { href: "" } },
  document: { querySelector() { return null; } },
  encodeURIComponent,
  fetch: () => new Promise(() => {}),
  console
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(labelsSource, context);
vm.runInContext(librarySource, context);
context.testRun = { feedstock_id: "lunar_mare_low_ti" };
process.stdout.write(JSON.stringify(vm.runInContext("runMetaLine(testRun)", context)));
"""

    completed = subprocess.run(
        ["node", "-", str(root / "labels.js"), str(root / "library.js")],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(completed.stdout) == ["Lunar Mare Low Ti"]


def test_library_preserves_focus_across_rerenders() -> None:
    root = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const librarySource = fs.readFileSync(process.argv[3], "utf8");
const body = { dataset: {}, isConnected: true };
const document = { activeElement: body };
const controls = { folder: new Map(), star: new Map() };
    function disconnect(map) { for (const control of map.values()) { control.isConnected = false; if (document.activeElement === control) document.activeElement = body; } map.clear(); }
function control(kind, value) { return { dataset: { [kind]: value }, isConnected: true, focus() { if (this.isConnected) document.activeElement = this; }, closest(selector) { return selector === `[data-${kind}]` ? this : null; } }; }
function element(id, kind) { return { id, value: "", listeners: {}, _html: "", addEventListener(type, fn) { this.listeners[type] = fn; }, focus() { document.activeElement = this; }, set innerHTML(value) { this._html = value; if (!kind) return; disconnect(controls[kind]); for (const match of value.matchAll(new RegExp(`data-${kind}=\"([^\"]+)\"`, "g"))) controls[kind].set(match[1], control(kind, match[1])); }, get innerHTML() { return this._html; } }; }
const elements = { library: element("library") };
Object.defineProperty(elements.library, "innerHTML", { get() { return this.libraryHTML || ""; }, set(value) { this.libraryHTML = value; elements["folder-list"] = element("folder-list", "folder"); elements["run-list"] = element("run-list", "star"); elements["run-filter"] = element("run-filter"); elements["run-sort"] = element("run-sort"); elements["run-sort"].value = "created"; } });
document.querySelector = (selector) => elements[selector.slice(1)] || null;
document.querySelectorAll = (selector) => selector === "[data-folder]" ? [...controls.folder.values()] : [...controls.star.values()];
const context = { window: { location: { href: "" } }, document, encodeURIComponent, fetch: async () => ({ ok: true, json: async () => ({ starred: true }) }), console };
context.globalThis = context; vm.createContext(context); vm.runInContext(labelsSource, context); vm.runInContext(librarySource, context);
vm.runInContext("render([{run_id:'focus-run',name:'Focus',feedstock_id:'lunar_mare_low_ti',status:'ok',folder:'Favorites',live:true,starred:false}])", context);
    (async () => { const click = elements.library.listeners.click; const star = controls.star.get("focus-run"); star.focus(); await click({ target: star }); const starFocus = document.activeElement.dataset.star; const folder = controls.folder.get("Favorites"); folder.focus(); await click({ target: folder }); const folderFocus = document.activeElement.dataset.folder; process.stdout.write(JSON.stringify({ folderFocus, starFocus })); })();
"""
    completed = subprocess.run(
        ["node", "-", str(root / "labels.js"), str(root / "library.js")],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout) == {"folderFocus": "Favorites", "starFocus": "focus-run"}


def test_settings_yaml_export_preserves_numbers_through_pyyaml() -> None:
    settings_js = Path(__file__).resolve().parents[1] / "web/report_viewer/settings.js"
    cases = {"small": 1e-9, "neg_small": -1e-9, "big": 1e21, "mantissa": 1.5e-7, "plain_float": 0.001, "with_frac": 1234.5, "zero": 0, "int": 42}
    harness = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const ctx = { globalThis: {} }; ctx.globalThis = ctx; vm.createContext(ctx);
const slice = src.match(/function yamlScalar[\s\S]*?\n}\n\nfunction toYaml[\s\S]*?\n}\n/);
vm.runInContext(slice[0], ctx);
const cases = JSON.parse(process.argv[3]); const out = {};
for (const [key, value] of Object.entries(cases)) out[key] = ctx.yamlScalar(value);
process.stdout.write(JSON.stringify(out));
"""
    completed = subprocess.run(["node", "-", str(settings_js), json.dumps(cases)], input=harness, text=True, capture_output=True, check=True)
    emitted = json.loads(completed.stdout)
    for key, original in cases.items():
        parsed = yaml.safe_load(emitted[key])
        assert isinstance(parsed, (int, float)) and not isinstance(parsed, bool)
        assert abs(parsed - original) <= abs(original) * 1e-12 + 1e-18, (key, emitted[key], parsed)


_PORTED_PANELS: list[str] = ["p1-fe-redox", "p2-taps", "p3-wall-coating", "p4-stage-purity", "p5-alkali-shuttle", "p6-mre", "p7-energy", "p8-cost-rollup", "p9-provenance", "p10-vapor-source", "p11-deliverables", "p12-carrier-pressure", "p13-status-strip", "p14-sankey", "p15-equipment-diagram"]


@pytest.mark.parametrize("sample", ["populated", "zero", "partial"])
def test_registered_modules_render_and_scrub_in_target_shell(sample: str) -> None:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    artifact = root / "sample-run-artifact.json" if sample == "populated" else _panel_artifact()
    if sample == "partial":
        artifact["timesteps"] = [
            {"hour": 0, "summary": {"campaign": "<script>hostile</script>"}},
            {"hour": 7, "summary": {"campaign": "C5", "T_C": 1500}},
        ]
    state = _render_report_state_with_panels(artifact)
    ids = [f"sec-{name}" for name in _PORTED_PANELS]
    assert state["scripts"] == ["labels.js", *[f"panels/{name}.js" for name in _PORTED_PANELS], "report-viewer.js"]
    styles = re.findall(r'<link rel="stylesheet" href="\./([^"]+)">', (root / "index.html").read_text())
    assert styles == ["report-viewer.css", *[f"panels/{name}.css" for name in _PORTED_PANELS]]
    assert [call["id"] for call in state["calls"]["render"]] == ids
    assert all(call["summaries"] for call in state["calls"]["render"])
    assert not state["calls"]["errors"]
    assert "Could not read the frozen artifact" not in state["html"]
    assert "Panel failed to render" not in state["html"]
    for panel_id in ids:
        assert state["html"].count(f'id="{panel_id}"') == 1
    scrub_targets = {
        "p1-fe-redox": "sec-p1-selected-timestep-body",
        "p3-wall-coating": "sec-p3-wall-coating-hourly",
        "p13-status-strip": "p13-status-strip-live",
        "p15-equipment-diagram": "p15-equipment-state",
    }
    if sample == "zero":
        assert state["calls"]["scrub"] == []
        assert "stepper" not in state["snapshots"][0]
        for panel_id in ids:
            section = state["html"].split(f'id="{panel_id}"', 1)[1].split("</section>", 1)[0]
            assert re.search(r"pending|not emitted|unavailable|no timestep", section, re.I)
    else:
        source = json.loads(artifact.read_text()) if isinstance(artifact, Path) else artifact
        last = len(source["timesteps"]) - 1
        consumers = [name for name in _PORTED_PANELS if name in scrub_targets]
        assert state["calls"]["scrub"] == [[f"sec-{name}", index] for index in [0, last, 0] for name in consumers]
        assert state["snapshots"][1]["step-output"].startswith(f'Hour {source["timesteps"][last]["hour"]} ·')
        for name in consumers:
            target = scrub_targets[name]
            assert state["snapshots"][0][target]
            assert state["snapshots"][0][target] != state["snapshots"][1][target]
            assert state["snapshots"][0][target] == state["snapshots"][2][target]
        if "p13-status-strip" in _PORTED_PANELS:
            assert state["pinned"]
    assert "<script>hostile</script>" not in state["html"]


def test_registry_dispatch_contains_throwing_render_and_scrub() -> None:
    artifact = _panel_artifact()
    artifact["timesteps"] = [{"hour": 0, "summary": {}}, {"hour": 8, "summary": {}}]
    state = _render_report_state_with_panels(artifact, """
globalThis.ReportPanels = [null,
  {id: '<bad>', render() { throw new Error('<failure>'); }, onTimestep() { throw new Error('scrub failed'); }},
  {id: 'good', render() { return '<section id="good">still rendered</section>'; },
    onTimestep(artifact, index) { document.getElementById('good').innerHTML = String(index); }}];
""")
    assert 'id="good"' in state["html"]
    assert "&lt;failure&gt;" in state["html"]
    assert "<bad>" not in state["html"]
    assert [snapshot["good"] for snapshot in state["snapshots"]] == ["0", "1", "0"]
    assert len(state["calls"]["errors"]) == 3


def _panel_artifact() -> dict:
    return {
        "artifact_schema_version": "0.2.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "panel-registry"},
        "timesteps": [],
        "terminal": {"final_state": {}},
    }


@pytest.mark.parametrize(
    "panels_js,expect_notice",
    [
        ("globalThis.ReportPanels = [null];", True),
        ("globalThis.ReportPanels = [{ id: 'p', render() { return { html: 'x' }; } }];", True),
        ("globalThis.ReportPanels = [{ id: 'p', render() { return 42; } }];", True),
        ("globalThis.ReportPanels = [{ id: 'p' }];", True),
        ("globalThis.ReportPanels = [{ id: 'p', render() { return undefined; } }];", False),
    ],
)
def test_panel_registry_contains_bad_panels(panels_js: str, expect_notice: bool) -> None:
    """A misbehaving panel costs its own section, never the whole report.

    A null entry previously made the catch block itself throw on `panel.id`,
    which escaped containment and replaced the entire report with the fatal
    panel; a non-string return was coerced by join() into "[object Object]".
    """
    html = _render_report_html_with_panels(_panel_artifact(), panels_js)

    assert "[object Object]" not in html
    # The report itself still rendered.
    assert "Evolved metal mass" in html or "Full terminal ledger" in html
    assert "Could not read the frozen artifact" not in html
    if expect_notice:
        assert "Panel failed to render" in html
    else:
        assert "Panel failed to render" not in html
