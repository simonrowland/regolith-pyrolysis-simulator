from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"


def _run_panel(artifact: dict, update_indexes: list[int] | None = None) -> dict:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const panelSource = fs.readFileSync(process.argv[3], "utf8");
const artifact = JSON.parse(process.argv[4]);
const indexes = JSON.parse(process.argv[5]);
const live = { innerHTML: "" };
const panelSection = {};
const timestepSection = {
  nextElementSibling: null,
  inserted: 0,
  insertAdjacentElement(position, element) {
    if (position !== "afterend") throw new Error(`unexpected position ${position}`);
    this.nextElementSibling = element;
    this.inserted += 1;
  }
};
const stepper = { closest(selector) { return selector === "section" ? timestepSection : null; } };
const context = {
  console,
  document: {
    querySelector(selector) {
      return {
        "#p13-status-strip-live": live,
        "#sec-p13-status-strip": panelSection,
        ".stepper": stepper
      }[selector] || null;
    }
  }
};
context.globalThis = context;
vm.runInNewContext(labelsSource, context);
vm.runInNewContext(panelSource, context);
const panel = context.ReportPanels.find((entry) => entry.id === "sec-p13-status-strip");
if (!panel) throw new Error("P13 panel did not register");
const rendered = panel.render(artifact, [], [], {});
const updates = indexes.map((index) => {
  panel.onTimestep(artifact, index);
  return live.innerHTML;
});
process.stdout.write(JSON.stringify({
  id: panel.id,
  hasRender: typeof panel.render === "function",
  hasOnTimestep: typeof panel.onTimestep === "function",
  rendered,
  updates,
  inserted: timestepSection.inserted
}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(VIEWER / "labels.js"),
            str(VIEWER / "panels" / "p13-status-strip.js"),
            json.dumps(artifact),
            json.dumps(update_indexes or []),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _present_summary() -> dict:
    return {
        "campaign": "C5",
        "fe_redox_split": {
            "fO2_log": -9.0,
            "iw_log": -71.6003,
            "fe3_over_sigma_fe": 0.9984,
            "ferric_frac": 0.9984,
            "ferrous_frac": 0.0016,
            "native_fe_frac": 0.0,
            "status": "diagnostic",
            "source": "backend redox ledger",
            "reference": "IW buffer",
            "authoritative": False,
            "diagnostic_only": True,
            "extrapolation": True,
            "high_uncertainty": True,
        },
        "regime": "viscous",
        "Kn": {"knudsen_number": 0.0038201},
        "transport_formula_id": "bernoulli_swept_v2",
        "mre_ellingham_ladder_diagnostic": {
            "declared_rung_V": 4.75,
            "certification": "diagnostic_uncertified",
            "authority": "authoritative_ellingham_graph_with_static_fallback",
            "derived_Ed_V": {"NiO": 4.7521, "FeO": 1.231},
        },
    }


def test_p13_registers_renders_emitted_facts_and_pins_on_update() -> None:
    artifact = {"timesteps": [{"hour": 1, "summary": _present_summary()}]}
    result = _run_panel(artifact, [0])
    html = result["rendered"]
    update = result["updates"][0]

    assert result["id"] == "sec-p13-status-strip"
    assert result["hasRender"] and result["hasOnTimestep"]
    assert result["inserted"] == 1
    assert "log fO₂ -9" in html
    assert "ΔIW -71.6" in html
    assert "Fe³⁺/ΣFe" in html
    assert "authoritative: false" in html
    assert "diagnostic_only" in html
    assert "extrapolation" in html
    assert "high_uncertainty" in html
    assert "viscous / swept" in html
    assert "Kn 0.00382" in html
    assert "bernoulli_swept_v2" in html
    assert "Campaign: C5" in html
    assert "Ladder 4.75 V" in html
    assert "diagnostic_uncertified" in html
    assert "authoritative_ellingham_graph_with_static_fallback" in html
    assert "NiO: 4.752 V" in html
    assert "MRE activity: not emitted" in html
    assert "viscous / swept" in update


def test_p13_on_timestep_replaces_present_values_with_honest_pending() -> None:
    artifact = {
        "timesteps": [
            {"hour": 1, "summary": _present_summary()},
            {
                "hour": 2,
                "summary": {
                    "campaign": "C6",
                    "regime": "",
                    "Kn": None,
                    "transport_formula_id": "not_applicable_until_p0",
                },
            },
        ]
    }
    result = _run_panel(artifact, [0, 1])
    first, second = result["updates"]

    assert "Ladder 4.75 V" in first
    assert "redox not emitted" in second
    assert "authority not emitted" in second
    assert "regime not emitted" in second
    assert "Kn not emitted" in second
    assert "Campaign: C6" in second
    assert "ladder diagnostic: not emitted" in second
    assert "MRE activity: not emitted" in second
    assert "4.75 V" not in second
    assert result["inserted"] == 1


def test_p13_failed_ladder_surfaces_sentinel_and_withholds_voltage_evidence() -> None:
    summary = _present_summary()
    summary["mre_ellingham_ladder_diagnostic"] = {
        "declared_rung_V": 99.9,
        "derived_Ed_V": {"FeO": 88.8},
        "certification": "diagnostic_uncertified",
        "authority": "static_fallback",
        "status": "diagnostic_failed:solver_unavailable",
    }
    result = _run_panel({"timesteps": [{"summary": summary}]}, [0])
    html = result["updates"][0]

    assert "diagnostic_failed:solver_unavailable" in html
    assert "diagnostic_uncertified" in html
    assert "static_fallback" in html
    assert "withheld because the emitted diagnostic status reports failure" in html
    assert "99.9" not in html
    assert "88.8" not in html
    assert "MRE activity: not emitted" in html
    assert "MRE active" not in html
    assert "MRE inactive" not in html


def test_p13_flow_has_no_authority_chip_and_untrusted_values_are_escaped() -> None:
    summary = _present_summary()
    summary["campaign"] = '<img src=x onerror="boom">'
    summary["regime"] = "free_molecular"
    summary["transport_formula_id"] = '<script>alert("flow")</script>'
    result = _run_panel({"timesteps": [{"summary": summary}]})
    html = result["rendered"]
    flow = html.split('class="sec-p13-tile sec-p13-flow"', 1)[1].split(
        'class="sec-p13-tile sec-p13-mre"', 1
    )[0]

    assert "ballistic" in flow
    assert "authority" not in flow.lower()
    assert "sec-p13-chip" not in flow
    assert "&lt;script&gt;alert(&quot;flow&quot;)&lt;/script&gt;" in flow
    assert "<script>" not in flow
    assert "&lt;img src=x onerror=&quot;boom&quot;&gt;" in html
    assert "<img" not in html
    assert "MRE activity: not emitted" in html
