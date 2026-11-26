from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
NOTICE = {
    "kind": "vapour_batch_unresolved",
    "species": "Fe",
    "hour": 4,
    "reason": "surface closure refusal",
}


def _render_notice_summaries() -> tuple[str, str]:
    payload = {
        "status": "ok",
        "tick_view": "full",
        "n_requested": 1,
        "n_flux_active": 0,
        "n_refused": 1,
        "flux_overlay": {"notices": [NOTICE]},
        "vapour_rail_instrumentation": {
            "schema": "vapour_rail_instrumentation.v1",
            "vapour_batch": {"n_flux_active": 0, "n_requested": 1, "n_refused": 1},
            "flux_overlay": {"notices": [NOTICE]},
        },
        "condensation_refusals_by_species": {},
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const root = process.argv[2];
const payload = JSON.parse(process.argv[4]);
function element() {
  return {
    children: [], className: "", textContent: "",
    appendChild(child) { this.children.push(child); },
    removeChild(child) { this.children.splice(this.children.indexOf(child), 1); },
    get firstChild() { return this.children[0] || null; }
  };
}
const nodes = new Map();
const document = {
  readyState: "complete",
  querySelector: () => null,
  getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  },
  createElement: element
};
const context = {
  console, URLSearchParams, document,
  window: { location: { search: "" } },
  socket: { on() {}, emit() {} },
  fetch: () => new Promise(() => {})
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(`${root}/labels.js`, "utf8"), context);
vm.runInContext(fs.readFileSync(`${root}/report-viewer.js`, "utf8"), context);
context.__terminal = payload.vapour_rail_instrumentation
  ? { vapour_rail_instrumentation: payload.vapour_rail_instrumentation,
      condensation_refusals_by_species: payload.condensation_refusals_by_species }
  : {};
const reportHtml = vm.runInContext("vapourRailSection(__terminal)", context);
const reportMatch = reportHtml.match(/<span>Unavailable vapour flux<\/span><b>(.*?)<\/b>/);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
context.renderVapourRailInstrumentationPanel(payload);
const liveLines = document.getElementById("vapour-rail-instrumentation-content").children;
const liveLine = liveLines.find((line) => line.textContent.startsWith("Unavailable vapour flux: "));
process.stdout.write(JSON.stringify({
  report: reportMatch && reportMatch[1],
  live: liveLine && liveLine.textContent.slice("Unavailable vapour flux: ".length)
}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(ROOT / "web" / "report_viewer"),
            str(ROOT / "web" / "static" / "js" / "simulator-advisory.js"),
            json.dumps(payload),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    rendered = json.loads(completed.stdout)
    return rendered["live"], rendered["report"]


def test_unresolved_vapour_notice_summary_is_pinned_across_surfaces() -> None:
    expected = "Fe, hour 4: surface closure refusal"

    assert _render_notice_summaries() == (expected, expected)
