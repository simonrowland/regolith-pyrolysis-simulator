from __future__ import annotations

import json
from pathlib import Path
import re
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


def test_report_viewer_serves_index_and_assets(tmp_path: Path) -> None:
    client = _app(tmp_path).test_client()

    index = client.get("/report/")
    script = client.get("/report/settings.js")
    labels = client.get("/report/labels.js")

    assert index.status_code == 200
    assert b"Regolith Refinery Run Report" in index.data
    assert script.status_code == 200
    assert b"Download run.yaml" in script.data
    assert labels.status_code == 200
    assert b"fmtRunId" in labels.data


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


@pytest.mark.parametrize("include_activity", [False, True])
def test_report_viewer_presence_gates_stage_purity_activity(
    include_activity: bool,
) -> None:
    script_path = Path(__file__).resolve().parents[1] / "web/report_viewer/report-viewer.js"
    labels_path = script_path.with_name("labels.js")
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
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const context = {
  window: { location: { search: "" } },
  document: { querySelector: (selector) => selector === "#report" ? report : null },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""

    completed = subprocess.run(
        ["node", "-", str(labels_path), str(script_path), json.dumps(artifact)],
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


def test_report_viewer_no_rows_pumping_is_pending_not_measured_zero() -> None:
    root = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
    artifact = {
        "artifact_schema_version": "0.2.0",
        "execution_status": "partial",
        "lifecycle": "cancelled",
        "header": {"run_id": "zero-timestep"},
        "timesteps": [],
        "terminal": {
            "run_metadata": {
                "cost_rollup_diagnostic": {
                    "pumping_diagnostic": {
                        "status": "no_rows",
                        "rows": [],
                        "pumping_electrical_kWh": 0.0,
                    }
                }
            }
        },
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const context = {
  window: { location: { search: "" } },
  document: {
    title: "",
    querySelector: (selector) => selector === "#report" ? report : null,
    querySelectorAll: () => []
  },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
context.globalThis = context;
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(reportSource, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""

    completed = subprocess.run(
        [
            "node", "-", str(root / "labels.js"),
            str(root / "report-viewer.js"), json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    html = completed.stdout

    assert "Pumping energy</span><b>not computed — no rows" in html
    assert "Pumping status</span><b>no_rows" in html
    assert "Pumping energy</span><b><span title=\"0 kWh\">0 kWh" not in html


def test_report_viewer_surfaces_provisional_wall_geometry_authority() -> None:
    root = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
    artifact = {
        "artifact_schema_version": "0.2.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "provisional-wall"},
        "timesteps": [],
        "terminal": {
            "run_metadata": {
                "knudsen_regime_diagnostic": {
                    "stage_area_geometry_provenance_notice": {
                        "status": "provisional",
                        "source_class": "engineering-default",
                        "output_status": "status_bearing",
                        "message": "Wall area is uncertified <surface>.",
                    }
                }
            }
        },
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const context = {
  window: { location: { search: "" } },
  document: {
    title: "",
    querySelector: (selector) => selector === "#report" ? report : null,
    querySelectorAll: () => []
  },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
context.globalThis = context;
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(reportSource, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""

    def render(payload: dict) -> str:
        completed = subprocess.run(
            [
                "node", "-", str(root / "labels.js"),
                str(root / "report-viewer.js"), json.dumps(payload),
            ],
            input=harness,
            text=True,
            capture_output=True,
            check=True,
        )
        return completed.stdout

    html = render(artifact)
    assert "wall geometry provisional" in html
    assert "source engineering-default" in html
    assert "output status_bearing" in html
    assert "Wall area is uncertified &lt;surface&gt;." in html
    assert "Wall area is uncertified <surface>." not in html

    del artifact["terminal"]["run_metadata"]
    assert "wall geometry provisional" not in render(artifact)


def test_report_viewer_uses_legacy_source_side_o2_alias_with_metric_label() -> None:
    root = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
    artifact = {
        "artifact_schema_version": "0.2.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "legacy-o2", "feedstock_id": "lunar_mare_low_ti"},
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
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const nodes = new Map();
function el(id) {
  if (!nodes.has(id)) {
    nodes.set(id, {
      innerHTML: "", textContent: "", value: "0", disabled: false, style: {},
      attributes: {}, addEventListener() {},
      setAttribute(name, value) { this.attributes[name] = String(value); }
    });
  }
  return nodes.get(id);
}
const context = {
  window: { location: { search: "" } },
  document: {
    title: "",
    querySelector(selector) {
      if (selector === "#report") return report;
      if (selector.startsWith("#")) return el(selector.slice(1));
      if (selector === ".stepper") return el("stepper-root");
      if (selector === ".status-pill") return el("step-pill");
      return null;
    },
    querySelectorAll() { return []; }
  },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(labelsSource, context);
vm.runInContext(reportSource, context);
const fallbackCases = vm.runInContext(`[
  sourceSideO2({
    O2_source_side_potential_kg_cumulative: 1.5,
    O2_yield_kg_cumulative: 2.5
  }),
  sourceSideO2({
    O2_source_side_potential_kg_cumulative: " ",
    O2_yield_kg_cumulative: 2.5
  }),
  sourceSideO2({
    O2_source_side_potential_kg_cumulative: false,
    O2_yield_kg_cumulative: []
  })
]`, context);
setImmediate(() => process.stdout.write(JSON.stringify({
  html: report.innerHTML,
  current: el("current-grid").innerHTML,
  fallbackCases
})));
"""

    completed = subprocess.run(
        [
            "node", "-", str(root / "labels.js"),
            str(root / "report-viewer.js"), json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    rendered = json.loads(completed.stdout)
    html = rendered["html"]
    current = rendered["current"]

    assert html.count("4.25 kg") >= 2
    assert "4.25 kg" in current
    assert "source-side O₂ potential (emitted; not recovered)" in html
    assert "source-side O₂ potential (emitted; not recovered)" in current
    assert "O2_yield_kg_cumulative" not in html + current
    assert rendered["fallbackCases"] == [1.5, 2.5, None]


def test_report_viewer_renders_account_disposition_without_yield_claims() -> None:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    artifact = {
        "artifact_schema_version": "0.1.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {
            "run_id": "0123456789abcdef0123456789abcdef",
            "name": "Disposition fixture",
        },
        "timesteps": [],
        "terminal": {
            "final_state": {
                "process.condensation_train": {"SiO2": 1.978e-7},
                "process.cleaned_melt": {"Al2O3": 4.0},
                "process.wall_deposit_segment_stage_0_to_stage_1": {"Na2O": 0.25},
                "process.overhead_gas": {"CO2": 0.5},
                "process.reagent_inventory": {"Na": 2.0},
                "process.future_account": {"Fe": 3.0},
            }
        },
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const context = {
  window: { location: { search: "" } },
  document: { querySelector: (selector) => selector === "#report" ? report : null },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(reportSource, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""

    completed = subprocess.run(
        [
            "node", "-", str(root / "labels.js"),
            str(root / "report-viewer.js"), json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    html = completed.stdout

    assert "Product accounts" in html
    assert "Retained accounts" in html
    assert "Loss accounts" in html
    assert "Terminal inventory accounts" in html
    assert "Reagent cycle · excluded" in html
    assert "Unclassified accounts" in html
    assert '>Cleaned melt</span>' in html
    assert '>Wall deposit · stage 0→1</span>' in html
    assert ">process.cleaned_melt<" not in html
    assert "SiO₂" in html
    assert "1.98e-7 mol" in html
    assert "01234567…" in html
    assert "Account disposition — where terminal inventory sits at run end" in html
    assert "NOT per-species feedstock-yield fractions" in html
    assert "yield_disposition (pending)" in html
    # Group labels are account-role buckets, not origin/yield claims.
    assert "feedstock-origin" not in html
    assert "O2_source_side_potential_kg_cumulative" not in html
    assert "cumulative source-side potential · not recovered product" in html
    assert "not feedstock origin or recovered yield" in html
    assert '<span class="sect">03</span>Account disposition' in html
    assert '<span class="sect">04</span>Full terminal ledger' in html
    assert html.count('<span class="sect">03</span>') == 1



def test_report_viewer_empty_and_absent_sections_are_honest() -> None:
    """Zero-ts / empty maps must not render blank table bodies."""
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    artifact = {
        "artifact_schema_version": "0.1.0",
        "execution_status": "partial",
        "lifecycle": "cancelled",
        "failure": {"reason": "client_disconnected", "error_message": ""},
        "header": {
            "run_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "name": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "feedstock_id": "lunar_mare_low_ti",
        },
        "timesteps": [],
        "terminal": {
            "final_state": {},
            "stage_purity": {},
        },
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const context = {
  window: { location: { search: "" } },
  document: { querySelector: (selector) => selector === "#report" ? report : null },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(reportSource, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""
    completed = subprocess.run(
        [
            "node", "-", str(root / "labels.js"),
            str(root / "report-viewer.js"), json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    html = completed.stdout

    assert "<tbody></tbody>" not in html
    assert "zero timesteps" in html
    assert "Empty ledger" in html
    assert "terminal.final_state was emitted with no accounts" in html
    assert "Empty" in html and "stage_purity was emitted with no stages" in html
    assert "process.cleaned_melt is absent" in html
    assert "Untitled run" in html
    assert "aaaaaaaa…" in html or "aaaaaaaa" in html
    assert "Execution status: partial" in html
    sections = [
        "Evolved metal mass",
        "Process record",
        "Account disposition",
        "Full terminal ledger",
        "Campaign results",
        "Metal taps",
        "Wall risk",
        "Terminal ceramic",
        "Energy",
        "Provenance",
    ]
    positions = []
    for number, title in enumerate(sections, start=1):
        token = f'<span class="sect">{number:02d}</span>{title}'
        assert html.count(token) == 1
        positions.append(html.index(token))
    assert positions == sorted(positions)


def test_report_viewer_404_and_corrupt_payload_render_fatal() -> None:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const mode = process.argv[4];
const context = {
  window: { location: { search: "?run=missing-run" } },
  document: { querySelector: (selector) => selector === "#report" ? report : null },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => {
    if (mode === "404") return { ok: false, status: 404 };
    return { ok: true, status: 200, json: async () => { throw new SyntaxError("bad json"); } };
  }
};
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(reportSource, context);
setImmediate(() => process.stdout.write(report.innerHTML));
"""
    for mode, needle in (
        ("404", "No run artifact was found"),
        ("corrupt", "not valid JSON"),
    ):
        completed = subprocess.run(
            ["node", "-", str(root / "labels.js"), str(root / "report-viewer.js"), mode],
            input=harness,
            text=True,
            capture_output=True,
            check=True,
        )
        html = completed.stdout
        assert "Report unavailable" in html
        assert needle in html
        assert 'href="./library.html"' in html


def test_report_viewer_css_has_responsive_and_dark_layout_guards() -> None:
    css = (
        Path(__file__).resolve().parents[1] / "web/report_viewer/report-viewer.css"
    ).read_text(encoding="utf-8")
    assert "overflow-x: clip" in css
    assert "@media (max-width: 768px)" in css
    assert "@media (max-width: 420px)" in css
    assert "@media (prefers-color-scheme: dark)" in css
    assert ".status-banner.failed" in css
    assert "word-break: break-word" in css
    assert ".table-wrap" in css and "overflow-x: auto" in css


def test_demo_note_light_mode_contrast_meets_wcag_aa() -> None:
    css = (
        Path(__file__).resolve().parents[1] / "web/report_viewer/report-viewer.css"
    ).read_text(encoding="utf-8")
    light_css, dark_css = css.split("@media (prefers-color-scheme: dark)", maxsplit=1)
    colors = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", light_css))
    demo_rule = re.search(
        r"(?m)^\.demo-note\s*\{[^}]*color:\s*var\(--([\w-]+)\)",
        light_css,
    )
    assert demo_rule is not None
    assert demo_rule.group(1) == "warning-ink"
    foreground = colors[demo_rule.group(1)]

    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return sum(weight * channel for weight, channel in zip((0.2126, 0.7152, 0.0722), linear))

    def contrast(left: str, right: str) -> float:
        lighter, darker = sorted((luminance(left), luminance(right)), reverse=True)
        return (lighter + 0.05) / (darker + 0.05)

    assert contrast(foreground, colors["paper"]) >= 4.5
    assert contrast(foreground, colors["panel"]) >= 4.5
    assert re.search(r"\.demo-note\s*\{\s*color:\s*#ffc767;\s*\}", dark_css)


def test_library_headline_chips_respect_yield_semantics_and_hide_hash_titles() -> None:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    index = [
        {
            "run_id": "0123456789abcdef0123456789abcdef",
            "name": "0123456789abcdef0123456789abcdef",
            "status": "ok",
            "lifecycle": "complete",
            "folder": "My runs",
            "summary": "fixture",
            "headline_yields_kg": {"Fe": 12.5, "O2": 4.25},
            "headline_yield_semantics": {
                "Fe": "evolved_product",
                "O2": "source_side_potential",
            },
            "live": True,
            "starred": False,
        },
        {
            "run_id": "demo-named-run",
            "name": "Named demo",
            "status": "ok",
            "lifecycle": "complete",
            "folder": "Default runs",
            "summary": "no yields",
            "live": False,
            "artifact": "index.html",
            "starred": False,
        },
    ]
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const library = { innerHTML: "", addEventListener() {} };
const nodes = new Map();
const context = {
  window: { location: { href: "" } },
  document: {
    querySelector(selector) {
      if (selector === "#library") return library;
      if (!nodes.has(selector)) {
        const el = {
          value: selector === "#run-sort" ? "created" : "",
          innerHTML: "",
          addEventListener() {}
        };
        nodes.set(selector, el);
      }
      return nodes.get(selector);
    }
  },
  URLSearchParams,
  encodeURIComponent,
  fetch: async (url) => {
    if (String(url).includes("runs-index")) {
      return { ok: true, json: async () => JSON.parse(process.argv[4]) };
    }
    return { ok: false, status: 503, json: async () => ({}) };
  }
};
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(source, context);
// Static + live fetch chain needs a macrotask after promise settlement.
setTimeout(() => {
  const list = nodes.get("#run-list");
  process.stdout.write(JSON.stringify({
    library: library.innerHTML,
    list: list ? list.innerHTML : ""
  }));
}, 50);
"""
    completed = subprocess.run(
        [
            "node", "-",
            str(root / "labels.js"),
            str(root / "library.js"),
            json.dumps(index),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    html = result["library"] + result["list"]

    assert "Untitled run · 01234567…" in html
    # Full hash may remain in title= tooltips / data-* only — never as the H2 label.
    assert "<h2>0123456789abcdef0123456789abcdef</h2>" not in html
    assert ">Untitled run · 01234567…</h2>" in html
    assert "Named demo" in html
    assert "evolved" in html
    assert "source-side potential (not recovered)" in html
    assert "not recovered" in html
    assert 'aria-label="Load report for Untitled run · 01234567…"' in html
    assert 'aria-labelledby="indexed-runs-heading"' in result["library"]


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


def test_report_viewer_stepper_exposes_keyboard_controls() -> None:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    artifact = {
        "artifact_schema_version": "0.1.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {"run_id": "stepper-a11y", "name": "Stepper fixture"},
        "timesteps": [
            {
                "hour": 1,
                "summary": {
                    "campaign": "C0",
                    "T_C": 1000,
                    "pO2_bar": 1e-6,
                    "metal_yields_kg": {"Fe": 0.1},
                    "O2_metric_label": "source-side O2 potential (emitted; not recovered)",
                    "O2_source_side_potential_kg_cumulative": 0.01,
                    "energy_electrical_kWh": 1.0,
                    "energy_evaporation_thermal_kWh": 0.5,
                    "regime": "free-molecular",
                },
            },
            {
                "hour": 2,
                "summary": {
                    "campaign": "C0",
                    "T_C": 1100,
                    "pO2_bar": 1e-6,
                    "metal_yields_kg": {"Fe": 0.2},
                    "O2_metric_label": "source-side O2 potential (emitted; not recovered)",
                    "O2_source_side_potential_kg_cumulative": 0.02,
                    "energy_electrical_kWh": 1.0,
                    "energy_evaporation_thermal_kWh": 0.5,
                    "regime": "free-molecular",
                },
            },
        ],
        "terminal": {
            "final_state": {
                "process.condensation_train": {"SiO2": 0.1},
            }
        },
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const listeners = {};
const elements = {
  "#report": report,
  "#stepper": {
    value: "0",
    addEventListener(type, fn) { listeners.stepper = listeners.stepper || {}; listeners.stepper[type] = fn; },
    setAttribute() {},
  },
  "#step-prev": {
    disabled: false,
    addEventListener(type, fn) { listeners.prev = listeners.prev || {}; listeners.prev[type] = fn; },
  },
  "#step-next": {
    disabled: false,
    addEventListener(type, fn) { listeners.next = listeners.next || {}; listeners.next[type] = fn; },
  },
  "#step-output": { textContent: "" },
  "#step-position": { textContent: "" },
  "#current-grid": { innerHTML: "" },
  "#timestep-ledger": { innerHTML: "" },
};
const context = {
  window: { location: { search: "" } },
  document: {
    querySelector(selector) { return elements[selector] || null; },
    querySelectorAll() { return []; }
  },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(JSON.stringify({
  html: report.innerHTML,
  hasPrev: Boolean(listeners.prev && listeners.prev.click),
  hasNext: Boolean(listeners.next && listeners.next.click),
  hasInput: Boolean(listeners.stepper && listeners.stepper.input)
})));
"""
    completed = subprocess.run(
        [
            "node", "-",
            str(root / "labels.js"),
            str(root / "report-viewer.js"),
            json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    html = result["html"]

    assert 'id="step-prev"' in html
    assert 'id="step-next"' in html
    assert 'aria-label="Previous hour"' in html
    assert 'aria-label="Next hour"' in html
    assert 'aria-valuetext="Hour 1 of 2"' in html or "aria-valuetext" in html
    assert "Evolved metal mass — Ellingham order" in html
    assert "Extraction yields" not in html
    assert "Product accounts" in html
    assert result["hasPrev"] and result["hasNext"] and result["hasInput"]
    assert "source-side O₂ potential (emitted; not recovered)" in html
    assert "O2_source_side_potential_kg_cumulative" not in html


def test_report_labels_format_scientific_values_and_identifiers() -> None:
    labels_path = (
        Path(__file__).resolve().parents[1] / "web/report_viewer/labels.js"
    )
    harness = r"""
require(process.argv[2]);
const {
  fmtNum, fmtRunId, prettySpecies, accountLabel, prettyFeedstock, prettyChemText
} = globalThis.ReportLabels;
process.stdout.write(JSON.stringify({
  zero: fmtNum(0, "mol"),
  trace: fmtNum(1.978e-7, "mol"),
  normal: fmtNum(12345.678, "kg"),
  missing: fmtNum(null, "kg"),
  hash: fmtRunId("0123456789abcdef0123456789abcdef"),
  named: fmtRunId("sample-fullseq-lunar-197h"),
  species: ["SiO2", "Al2O3", "Fe"].map(prettySpecies),
  wall: accountLabel("process.wall_deposit_segment_stage_3_to_stage_4"),
  unknown: accountLabel("process.future_account"),
  feedstock: prettyFeedstock("lunar_mare_low_ti"),
  chem: prettyChemText("source-side O2 potential (emitted; not recovered)")
}));
"""
    completed = subprocess.run(
        ["node", "-", str(labels_path)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)

    assert result["zero"] == "0 mol"
    assert result["trace"] == "1.98e-7 mol"
    assert result["normal"].endswith(" kg")
    assert "12345.678" not in result["normal"]
    assert result["missing"] == "not emitted"
    assert result["hash"] == "01234567…"
    assert result["named"] == "sample-fullseq-lunar-197h"
    assert result["species"] == ["SiO₂", "Al₂O₃", "Fe"]
    assert result["wall"] == "Wall deposit · stage 3→4"
    assert result["unknown"] == "Future Account"
    assert result["feedstock"] == "Lunar Mare Low Ti"
    assert result["chem"] == "source-side O₂ potential (emitted; not recovered)"


def test_report_viewer_section_order_and_stepper_controls() -> None:
    """Report page: sequential sections, readable feedstock, stepper affordances."""
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    artifact = {
        "artifact_schema_version": "0.2.0",
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {
            "run_id": "aabbccddeeff00112233445566778899",
            "name": "aabbccddeeff00112233445566778899",
            "feedstock_id": "lunar_mare_low_ti",
            "charge_mass_kg": 1000.0,
            "campaign_chain": ["C0", "C2A"],
            "engine_identity": {"name": "internal-analytical"},
            "cost_block": {
                "electrical_cost_per_kWh": 10.0,
                "solar_heat_cost_per_kWh": 0.05,
                "provenance": "test prices",
            },
        },
        "timesteps": [
            {
                "hour": 1,
                "summary": {
                    "campaign": "C0",
                    "T_C": 75.0,
                    "pO2_bar": 1e-8,
                    "P_total_bar": 0.01,
                    "energy_electrical_kWh": 0.001,
                    "energy_evaporation_thermal_kWh": 0.0,
                    "energy_latent_kWh": 0.0,
                    "energy_dissociation_kWh": 0.0,
                    "metal_yields_kg": {"Fe": 0.0},
                    "O2_source_side_potential_kg_cumulative": 0.0,
                    "O2_metric_label": "source-side O2 potential (emitted; not recovered)",
                    "regime": "viscous",
                    "Kn": 0.01,
                },
                "ledger": {"process.cleaned_melt": {"SiO2": 10.0}},
            },
            {
                "hour": 2,
                "summary": {
                    "campaign": "C2A",
                    "T_C": 1400.0,
                    "pO2_bar": 1e-6,
                    "P_total_bar": 0.01,
                    "energy_electrical_kWh": 0.002,
                    "energy_evaporation_thermal_kWh": 0.1,
                    "energy_latent_kWh": 0.05,
                    "energy_dissociation_kWh": 0.05,
                    "metal_yields_kg": {"Fe": 0.35979618445132294},
                    "O2_source_side_potential_kg_cumulative": 0.043710764798390075,
                    "O2_metric_label": "source-side O2 potential (emitted; not recovered)",
                    "regime": "viscous",
                    "Kn": 0.0003,
                },
                "ledger": {
                    "process.cleaned_melt": {"SiO2": 9.5, "Al2O3": 1.2},
                    "terminal.offgas": {"O2": 0.5},
                },
            },
        ],
        "terminal": {
            "final_state": {
                "process.cleaned_melt": {"SiO2": 9.5, "Al2O3": 1.2},
                "terminal.offgas": {"O2": 0.5},
            },
            "stage_purity": {
                "stage_1_fe_condenser": {
                    "label": "Fe Condenser",
                    "accepted_species": ["Fe"],
                    "total_kg": 0.36,
                    "designated_kg": 0.36,
                    "impurity_kg": 0.0,
                    "purity_fraction": 1.0,
                    "verdict": "PURE",
                }
            },
            "cost_totals": {
                "electrical_energy_kWh": 0.003,
                "evaporation_thermal_energy_kWh": 0.1,
                "electrical_cost_usd": 0.03,
                "solar_heat_cost_usd": 0.005,
                "total_cost_usd": 0.035,
                "process_electrical_energy_kWh": 0.003,
                "process_electrical_cost_usd": 0.03,
                "basis_note": "test basis",
            },
        },
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const reportSource = fs.readFileSync(process.argv[3], "utf8");
const report = { innerHTML: "" };
const nodes = new Map();
function el(id) {
  if (!nodes.has(id)) {
    nodes.set(id, {
      textContent: "",
      innerHTML: "",
      value: "0",
      disabled: false,
      style: {},
      attributes: {},
      addEventListener() {},
      setAttribute(name, value) { this.attributes[name] = String(value); },
      getAttribute(name) { return this.attributes[name]; }
    });
  }
  return nodes.get(id);
}
const context = {
  window: { location: { search: "?run=aabbccddeeff00112233445566778899" } },
  document: {
    title: "",
    querySelector(selector) {
      if (selector === "#report") return report;
      if (selector.startsWith("#")) return el(selector.slice(1));
      if (selector === ".status-pill") return el("step-pill");
      if (selector === ".stepper") return el("stepper-root");
      return null;
    },
    querySelectorAll() { return []; }
  },
  URLSearchParams,
  encodeURIComponent,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
context.globalThis = context;
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(reportSource, context);
setTimeout(() => {
  process.stdout.write(JSON.stringify({
    html: report.innerHTML,
    title: context.document.title,
    aria: el("stepper").attributes
  }));
}, 30);
"""
    completed = subprocess.run(
        [
            "node", "-", str(root / "labels.js"),
            str(root / "report-viewer.js"), json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    result = json.loads(completed.stdout)
    html = result["html"]

    # Readable title + feedstock (no raw 32-hex H1, no snake_case feedstock).
    assert "Untitled run · aabbccdd…" in html
    assert "Lunar Mare Low Ti" in html
    assert "lunar_mare_low_ti" in html  # retained in title tooltip
    assert "source-side O₂ potential" in html
    assert "source-side O2 potential" not in html

    # Sequential section numbers in visual order.
    sects = [
        ("01", "Evolved metal mass"),
        ("02", "Process record"),
        ("03", "Account disposition"),
        ("04", "Full terminal ledger"),
        ("05", "Campaign results"),
        ("06", "Metal taps"),
        ("07", "Wall risk"),
        ("08", "Terminal ceramic"),
        ("09", "Energy"),
        ("10", "Provenance"),
    ]
    positions = []
    for num, title in sects:
        token = f'<span class="sect">{num}</span>{title}'
        assert token in html, f"missing section {num} {title}"
        positions.append(html.index(token))
    assert positions == sorted(positions), "sections out of visual order"
    assert html.count('<span class="sect">03</span>') == 1

    # Stage purity: human label visible, raw key only in title.
    assert "Fe Condenser" in html
    assert "title=\"stage_1_fe_condenser\"" in html
    assert "trace mono" not in html or "stage_1_fe_condenser" not in html.split("Fe Condenser")[1][:80]

    # Stepper affordances.
    assert 'id="step-prev"' in html
    assert 'id="step-next"' in html
    assert 'id="stepper"' in html
    assert "aria-valuetext" in result["aria"] or "aria-valuenow" in result["aria"]

    # Footer navigation.
    assert 'href="./library.html"' in html
    assert "Captured settings" in html

    # Numbers stay scientific (4 sig / sci), not full double dumps in visible kg.
    assert "0.3598 kg" in html or "0.3598" in html
    assert "0.35979618445132294 kg" not in html.replace('title="0.35979618445132294 kg"', "")


@pytest.mark.parametrize("has_recipe_snapshot", [True, False])
def test_settings_script_executes_live_run_resolution_and_manifest_gate(
    has_recipe_snapshot: bool,
) -> None:
    script_path = Path(__file__).resolve().parents[1] / "web/report_viewer/settings.js"
    labels_path = script_path.with_name("labels.js")
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
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
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
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(JSON.stringify({ fetched, html: settings.innerHTML })));
"""

    completed = subprocess.run(
        [
            "node", "-", str(labels_path), str(script_path),
            "?run=run/live", json.dumps(artifact),
        ],
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


def test_library_renders_readable_cards_and_live_fallback() -> None:
    """Library page: hash titles truncated, yields via fmtNum, O₂ source-side, fallback."""
    root = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
    static_runs = json.loads((root / "runs-index.json").read_text(encoding="utf-8"))
    live_runs = [
        {
            "run_id": "45454a0a69b44ca5a747e950a662ff8b",
            "name": "45454a0a69b44ca5a747e950a662ff8b",
            "feedstock_id": "lunar_mare_low_ti",
            "campaign_chain": ["C0", "C6"],
            "peak_T_C": 1750.0,
            "headline_yields_kg": {
                "Fe": 0.35979618445132294,
                "O2": 0.043710764798390075,
            },
            "status": "ok",
            "lifecycle": "complete",
            "created_at": "2026-07-19T16:00:00Z",
            "starred": False,
            "summary": "Fe 0.35979618445132294 kg · O₂ (source-side) 0.043710764798390075 kg",
            "hours": 61,
        },
        {
            "run_id": "fde0cd985c7e436da8b6c026758d28ce",
            "name": "fde0cd985c7e436da8b6c026758d28ce",
            "feedstock_id": "lunar_mare_low_ti",
            "headline_yields_kg": {},
            "status": "partial",
            "lifecycle": "cancelled",
            "created_at": "2026-07-19T16:52:27Z",
            "starred": False,
            "summary": "",
        },
        {
            "run_id": "tiny-trace-run",
            "name": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "feedstock_id": "lunar_mare_low_ti",
            "headline_yields_kg": {"Fe": 1.627767869768048e-11, "O2": 0.0},
            "status": "ok",
            "lifecycle": "complete",
            "created_at": "2026-07-18T00:00:00Z",
            "starred": False,
            "summary": "Fe 1.62777e-11 kg",
        },
    ]
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const librarySource = fs.readFileSync(process.argv[3], "utf8");
const staticRuns = JSON.parse(process.argv[4]);
const liveRuns = JSON.parse(process.argv[5]);
const liveOk = process.argv[6] === "1";

function mockEl(id) {
  return {
    id, _html: "", value: "", disabled: false, dataset: {}, listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; }
  };
}
const els = { library: mockEl("library") };
const known = ["folder-list", "run-list", "run-filter", "run-sort"];
Object.defineProperty(els.library, "innerHTML", {
  get() { return this._html || ""; },
  set(v) {
    this._html = v;
    for (const id of known) if (!els[id]) els[id] = mockEl(id);
  }
});
const sandbox = {
  window: { location: { href: "" } },
  document: {
    querySelector(sel) {
      const id = sel.startsWith("#") ? sel.slice(1) : sel;
      return els[id] || null;
    }
  },
  encodeURIComponent,
  fetch: async (url) => {
    if (url === "./runs-index.json") {
      return { ok: true, json: async () => staticRuns };
    }
    if (url === "/api/runs") {
      if (!liveOk) return { ok: false, status: 503, json: async () => ({ error: "down" }) };
      return { ok: true, json: async () => liveRuns };
    }
    throw new Error(`unexpected fetch ${url}`);
  },
  setTimeout,
  console
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(labelsSource, sandbox);
vm.runInContext(librarySource, sandbox);
setImmediate(() => {
  const list = (els["run-list"] && els["run-list"]._html) || "";
  const folders = (els["folder-list"] && els["folder-list"]._html) || "";
  process.stdout.write(JSON.stringify({ list, folders, shell: els.library._html || "" }));
});
"""

    def _render(live_ok: bool) -> dict:
        completed = subprocess.run(
            [
                "node",
                "-",
                str(root / "labels.js"),
                str(root / "library.js"),
                json.dumps(static_runs),
                json.dumps(live_runs),
                "1" if live_ok else "0",
            ],
            input=harness,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(completed.stdout)

    live = _render(True)
    html = live["list"]
    folders = live["folders"]

    # Hash-like names become short scientific titles, not 32-char line noise.
    assert "Untitled run · 45454a0a…" in html
    assert "Untitled run · fde0cd98…" in html
    assert "<h2>45454a0a69b44ca5a747e950a662ff8b</h2>" not in html
    assert "45454a0a…" in html  # truncated mono run id
    assert "CANCELLED" in html
    # Yield chips use fmtNum (≤4 sig figs / scientific for traces) as visible text.
    # Full precision may remain only in title= tooltips (exactNumber), never as the chip body.
    assert "0.3598 kg" in html
    assert ">0.35979618445132294" not in html
    assert "1.63e-11 kg" in html
    # O₂ honesty: source-side, never claimed as recovered product.
    assert "O₂ source-side potential (not recovered)" in html
    assert "not recovered" in html
    assert "O₂ recovered" not in html
    # No "unfiled" noise when folder absent; structured meta present.
    assert "unfiled" not in html
    assert "Lunar Mare Low Ti" in html
    assert "lunar_mare_low_ti" not in html
    assert "C0→C6" in html
    # Named static sample keeps human title; demo without artifact stays disabled.
    assert "Full-sequence lunar" in html
    assert "demo metadata — no artifact" in html
    assert 'data-load="index.html"' in html or "data-load=\"index.html\"" in html
    assert "disabled" in html
    # Folder counts + scannability line.
    assert "Showing" in html and "indexed run" in html
    assert 'data-folder="My runs"' in folders
    assert "folder-count" in folders
    # XSS: artifact-ish name must escape if ever present in title path — star label uses esc.
    assert "<script>" not in html

    fallback = _render(False)
    assert "Live run index unavailable" in fallback["list"]
    assert "Full-sequence lunar" in fallback["list"]
    assert "45454a0a" not in fallback["list"]


def test_library_meta_line_escapes_span_injection() -> None:
    """Regression: a run meta field containing '<span' must be escaped, not passed through raw.

    runMetaLine emits only plain strings (feedstock_id / campaign chain / summary / date), all
    untrusted artifact data. A prior substring bypass rendered any part containing '<span' as live
    HTML → stored XSS in the library card meta line.
    """
    root = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
    static_runs: list = []
    live_runs = [
        {
            "run_id": "xss-probe",
            "name": "xss-probe",
            # Hostile feedstock id carrying span+script markup.
            "feedstock_id": '<span onmouseover="alert(1)">pwn</span><script>alert(2)</script>',
            "campaign_chain": ["C0"],
            "status": "ok",
            "lifecycle": "complete",
            "created_at": "2026-07-20T00:00:00Z",
            "starred": False,
            "summary": "",
            "hours": 1,
        },
    ]
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const librarySource = fs.readFileSync(process.argv[3], "utf8");
const staticRuns = JSON.parse(process.argv[4]);
const liveRuns = JSON.parse(process.argv[5]);
function mockEl(id) {
  return {
    id, _html: "", value: "", disabled: false, dataset: {}, listeners: {},
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = v; }
  };
}
const els = { library: mockEl("library") };
const known = ["folder-list", "run-list", "run-filter", "run-sort"];
Object.defineProperty(els.library, "innerHTML", {
  get() { return this._html || ""; },
  set(v) { this._html = v; for (const id of known) if (!els[id]) els[id] = mockEl(id); }
});
const sandbox = {
  window: { location: { href: "" } },
  document: { querySelector(sel) { const id = sel.startsWith("#") ? sel.slice(1) : sel; return els[id] || null; } },
  encodeURIComponent,
  fetch: async (url) => {
    if (url === "./runs-index.json") return { ok: true, json: async () => staticRuns };
    if (url === "/api/runs") return { ok: true, json: async () => liveRuns };
    throw new Error(`unexpected fetch ${url}`);
  },
  setTimeout, console
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(labelsSource, sandbox);
vm.runInContext(librarySource, sandbox);
setImmediate(() => { process.stdout.write((els["run-list"] && els["run-list"]._html) || ""); });
"""
    completed = subprocess.run(
        ["node", "-", str(root / "labels.js"), str(root / "library.js"),
         json.dumps(static_runs), json.dumps(live_runs)],
        input=harness, text=True, capture_output=True, check=True,
    )
    html = completed.stdout
    lower_html = html.lower()
    # The raw markup must NOT appear; the escaped form must.
    assert "<span onmouseover" not in lower_html
    assert "<script>alert(2)" not in lower_html
    assert "&lt;span onmouseover" in lower_html
    assert "&lt;script&gt;alert(2)" in lower_html


def test_settings_script_readable_labels_and_honest_absent_fields() -> None:
    """Settings inspector must read as a scientific report, not line noise."""
    script_path = Path(__file__).resolve().parents[1] / "web/report_viewer/settings.js"
    labels_path = script_path.with_name("labels.js")
    kernel_sha = "12d65b4f9fa9d2ce452eb811655177116fa06da0"
    noisy = 0.011309733552923255
    artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "execution_status": "ok",
        "lifecycle": "complete",
        "header": {
            "run_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "name": "Full-sequence lunar demo",
            "recipe_snapshot": {
                "recipe_schema_version": "recipe-schema-v1",
                "pins": ["campaigns.C6.pO2_mbar"],
                "setpoints_patch": {
                    "campaigns": {"C6": {"pO2_mbar": 0.2}},
                },
            },
            "engine_identity": {
                "name": "internal-analytical",
                "backend_wire_token": "internal-analytical",
                "kernel_commit_sha": kernel_sha,
                "cache_version": None,
            },
            "c3_dose": {"Na_kg": 140.0, "Al2O3_kg": 1.25},
            "cost_block": {
                "electrical_cost_per_kWh": 10.0,
                "solar_heat_cost_per_kWh": 0.05,
                "provenance": "canonical defaults; payload carried no cost parameters",
            },
            "effective_config": {
                "campaigns.C0.atmosphere": {
                    "value": "hard_vacuum",
                    "source": "default",
                },
                "campaigns.C6.pO2_mbar": {"value": 0.2, "source": "override"},
                "denser_geometry.initial_throat_area_m2": {
                    "value": noisy,
                    "source": "default",
                },
            },
        },
        "timesteps": [],
        "terminal": {},
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const settings = { innerHTML: "" };
const download = { addEventListener() {} };
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
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[5]) })
};
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(settings.innerHTML));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(labels_path),
            str(script_path),
            "?run=settings-readable",
            json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    html = completed.stdout

    # Lede: human name + truncated run id (full hash only in title tooltip).
    assert "Full-sequence lunar demo" in html
    assert "aaaaaaaa…" in html
    assert 'title="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in html

    # Recipe snapshot is structured (not a raw JSON dump of the contract fields).
    assert "recipe-schema-v1" in html
    assert "campaigns.C6.pO2_mbar" in html
    assert "Setpoints patch" in html
    assert '"recipe_schema_version"' not in html

    # Engine identity: human labels, truncated sha, absent stays absent.
    assert "Kernel commit" in html
    assert "Engine cache version" in html
    assert "Backend wire token" in html
    assert "kernel_commit_sha" not in html
    assert "12d65b4f…" in html
    assert kernel_sha not in html.replace(f'title="{kernel_sha}"', "")
    assert ">null<" not in html
    assert "not emitted" in html

    # C3 dose: kg units, species subscripts, _kg suffix stripped.
    # Subtitle may say "not mol" (honest unit disclaimer); dose cells must be kg.
    assert "Al₂O₃" in html
    assert "<td>Na</td>" in html
    assert "140 kg" in html
    assert "1.25 kg" in html
    assert "Dose · kg" in html
    assert "140 mol" not in html
    assert "1.25 mol" not in html

    # Two-price block + provenance note (no silent defaults).
    assert "10 USD/kWh" in html
    assert "0.05 USD/kWh" in html
    assert "Price provenance:" in html
    assert "canonical defaults; payload carried no cost parameters" in html

    # Effective config: fmtNum shortens noise; overrides sort first + highlight.
    assert str(noisy) not in html.replace(f'title="{noisy}"', "")
    assert "config-override" in html
    override_pos = html.find("campaigns.C6.pO2_mbar")
    default_pos = html.find("campaigns.C0.atmosphere")
    assert 0 <= override_pos < default_pos

    # Manifest download offered only when snapshot is complete.
    assert "/api/runs/settings-readable/run.yaml" in html
    assert "Download run.yaml unavailable" not in html


def test_settings_script_gates_run_yaml_when_snapshot_malformed() -> None:
    script_path = Path(__file__).resolve().parents[1] / "web/report_viewer/settings.js"
    labels_path = script_path.with_name("labels.js")
    # pins must be string[]; a number pin mirrors the server 409 rule.
    artifact = _artifact(
        recipe_snapshot={
            "setpoints_patch": {},
            "pins": [1],
            "recipe_schema_version": "recipe-schema-v1",
        }
    )
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const source = fs.readFileSync(process.argv[3], "utf8");
const settings = { innerHTML: "" };
const download = { addEventListener() {} };
const context = {
  window: { location: { search: "?run=bad-snap" } },
  document: {
    querySelector(selector) {
      if (selector === "#settings") return settings;
      if (selector === "#download-run") return download;
      throw new Error(`unexpected selector ${selector}`);
    },
    createElement() { throw new Error("download should not execute during render"); }
  },
  URLSearchParams, Blob, URL, encodeURIComponent, setTimeout,
  fetch: async () => ({ ok: true, json: async () => JSON.parse(process.argv[4]) })
};
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(source, context);
setImmediate(() => process.stdout.write(settings.innerHTML));
"""
    completed = subprocess.run(
        ["node", "-", str(labels_path), str(script_path), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    html = completed.stdout
    assert "/api/runs/bad-snap/run.yaml" not in html
    assert "Download run.yaml unavailable" in html
    assert "409" in html


def test_report_viewer_serves_only_viewer_asset_types(tmp_path: Path) -> None:
    # send_from_directory alone would publish EVERY regular file in the
    # source dir — non-asset files (freeze_sample.py) and dotfiles must 404.
    client = _app(tmp_path).test_client()

    assert client.get("/report/freeze_sample.py").status_code == 404
    assert client.get("/report/.hidden.json").status_code == 404
    assert client.get("/report/sample-run-artifact.json").status_code == 200
    assert client.get("/report/library.html").status_code == 200
    assert b"/api/runs" in client.get("/report/library.html").data
    assert b"No engine or backend is used" not in client.get("/report/library.html").data


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
