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
CAPACITY_BOUND = {
    "kind": "capacity_solve_excludes_unavailable_species",
    "hour": 4,
    "excluded_species": [{
        "species": "Si",
        "reason": "missing Chapman-Enskog transport data",
    }],
    "bound_direction": {
        "resolved_fluxes": "upper",
        "headspace_pressure": "lower",
    },
}


def _render_notice_summaries(
    shared_summary: str | None = None,
) -> tuple[str, str, str, str]:
    payload = {
        "status": "ok",
        "tick_view": "full",
        "n_requested": 1,
        "n_flux_active": 0,
        "n_refused": 1,
        "flux_overlay": {"notices": [NOTICE, CAPACITY_BOUND]},
        "vapour_rail_instrumentation": {
            "schema": "vapour_rail_instrumentation.v1",
            "vapour_batch": {"n_flux_active": 0, "n_requested": 1, "n_refused": 1},
            "flux_overlay": {"notices": [NOTICE, CAPACITY_BOUND]},
        },
        "condensation_refusals_by_species": {},
        "shared_summary_mutation": shared_summary,
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
if (payload.shared_summary_mutation !== null) {
  context.ReportLabels = Object.freeze({
    ...context.ReportLabels,
    unresolvedVapourNoticeSummary: () => payload.shared_summary_mutation
  });
}
vm.runInContext(fs.readFileSync(`${root}/report-viewer.js`, "utf8"), context);
context.__terminal = payload.vapour_rail_instrumentation
  ? { vapour_rail_instrumentation: payload.vapour_rail_instrumentation,
      condensation_refusals_by_species: payload.condensation_refusals_by_species }
  : {};
const reportHtml = vm.runInContext("vapourRailSection(__terminal)", context);
const reportMatch = reportHtml.match(/<span>Unavailable vapour flux<\/span><b>(.*?)<\/b>/);
const reportBoundMatch = reportHtml.match(/<span>Capacity coupling bound<\/span><b>(.*?)<\/b>/);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
context.renderVapourRailInstrumentationPanel(payload);
const liveLines = document.getElementById("vapour-rail-instrumentation-content").children;
const liveLine = liveLines.find((line) => line.textContent.startsWith("Unavailable vapour flux: "));
const liveBoundLine = liveLines.find((line) => line.textContent.startsWith("Capacity coupling bound: "));
process.stdout.write(JSON.stringify({
  report: reportMatch && reportMatch[1],
  live: liveLine && liveLine.textContent.slice("Unavailable vapour flux: ".length),
  report_bound: reportBoundMatch && reportBoundMatch[1],
  live_bound: liveBoundLine && liveBoundLine.textContent.slice("Capacity coupling bound: ".length)
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
    return (
        rendered["live"],
        rendered["report"],
        rendered["live_bound"],
        rendered["report_bound"],
    )


def test_unresolved_vapour_notice_summary_is_pinned_across_surfaces() -> None:
    expected = "Fe, hour 4: surface closure refusal"
    expected_bound = (
        "Si: missing Chapman-Enskog transport data; resolved fluxes upper; "
        "headspace pressure lower"
    )

    assert _render_notice_summaries() == (
        expected,
        expected,
        expected_bound,
        expected_bound,
    )


def test_live_and_frozen_consumers_use_the_shared_notice_formatter() -> None:
    marker = "shared formatter test mutation"
    expected_bound = (
        "Si: missing Chapman-Enskog transport data; resolved fluxes upper; "
        "headspace pressure lower"
    )

    assert _render_notice_summaries(marker) == (
        marker,
        marker,
        expected_bound,
        expected_bound,
    )


def test_simulator_template_loads_shared_labels_before_advisory() -> None:
    template = (ROOT / "web" / "templates" / "simulator.html").read_text()

    labels_at = template.index("url_for('web.report_viewer', asset='labels.js'")
    advisory_at = template.index("js/simulator-advisory.js")
    assert labels_at < advisory_at
