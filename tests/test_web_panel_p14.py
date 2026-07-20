from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"


def _render_panel(artifact: dict, *, click_account: str = "") -> dict:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const panelSource = fs.readFileSync(process.argv[3], "utf8");
const artifact = JSON.parse(process.argv[4]);
const clickAccount = process.argv[5];
let clickHandler = null;
const localDetail = {
  open: false, removedId: false, scrolled: 0,
  removeAttribute(name) { if (name === "id") this.removedId = true; },
  scrollIntoView() { this.scrolled += 1; }
};
const accountSpan = { getAttribute(name) { return name === "title" ? clickAccount : null; } };
const sharedRow = {
  id: "", attributes: {}, scrolled: 0, focused: 0,
  querySelectorAll(selector) { return selector === "span[title]" ? [accountSpan] : []; },
  setAttribute(name, value) { this.attributes[name] = String(value); },
  scrollIntoView() { this.scrolled += 1; },
  focus() { this.focused += 1; }
};
const context = {
  console,
  document: {
    addEventListener(type, handler) { if (type === "click") clickHandler = handler; },
    getElementById() { return localDetail; },
    querySelectorAll(selector) {
      return selector === ".disposition-group tbody tr" && clickAccount ? [sharedRow] : [];
    }
  }
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(labelsSource, context);
vm.runInContext(panelSource, context);
const panel = context.ReportPanels[0];
const html = panel.render(artifact, [], [], {});
if (clickAccount && clickHandler) {
  const marker = `data-p14-account="${clickAccount}"`;
  const markerIndex = html.indexOf(marker);
  const prefix = markerIndex >= 0 ? html.slice(Math.max(0, markerIndex - 400), markerIndex) : "";
  const hrefs = [...prefix.matchAll(/href="([^"]+)"/g)];
  const href = hrefs.length ? hrefs[hrefs.length - 1][1] : "";
  const trigger = {
    getAttribute(name) {
      if (name === "data-p14-account") return clickAccount;
      if (name === "href") return href;
      return null;
    }
  };
  clickHandler({ target: { closest() { return trigger; } } });
}
process.stdout.write(JSON.stringify({
  id: panel.id,
  panelCount: context.ReportPanels.length,
  html,
  interaction: {
    handlerInstalled: Boolean(clickHandler),
    rowId: sharedRow.id,
    rowScrolled: sharedRow.scrolled,
    rowFocused: sharedRow.focused,
    rowTabIndex: sharedRow.attributes.tabindex || null,
    localIdRemoved: localDetail.removedId
  }
}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(VIEWER / "labels.js"),
            str(VIEWER / "panels" / "p14-sankey.js"),
            json.dumps(artifact),
            click_account,
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _artifact(terminal: dict) -> dict:
    return {"terminal": terminal}


def test_p14_renders_emitted_accounts_trace_scaling_o2_and_interaction() -> None:
    state = _render_panel(
        _artifact(
            {
                "final_state": {
                    "process.cleaned_melt": {"SiO2": 10_000.0},
                    "process.metal_phase_bottom_pool": {"Fe": 100.0, "Cr": 1.0},
                    "terminal.oxygen_melt_offgas_stored": {"O2": 1.0},
                    "terminal.oxygen_mre_anode_stored": {"O2": 2.0},
                    "terminal.offgas": {"Na": 3.0},
                }
            }
        ),
        click_account="terminal.offgas",
    )
    html = state["html"]

    assert state["id"] == "sec-p14-sankey"
    assert state["panelCount"] == 1
    assert "terminal inventory total (Σ accounts, mol — display total, not charge)" in html
    assert "mol basis" in html
    assert "widths √-scaled for readability — hover for true mol" in html
    assert 'class="sec-p14-row sec-p14-trace-node"' in html
    assert "trace inventory (2 species)" in html
    assert "trace inventory members — Metal pool (bottom) · Cr 1 mol; Offgas · Na 3 mol" in html
    assert "O₂ stored · melt offgas" in html
    assert "O₂ stored · MRE anode" in html
    assert html.count('data-p14-account="terminal.oxygen_melt_offgas_stored"') >= 2
    assert html.count('data-p14-account="terminal.oxygen_mre_anode_stored"') >= 2
    assert 'data-p14-account="terminal.glass"' not in html
    assert html.index('data-p14-account="process.metal_phase_bottom_pool"') < html.index(
        'data-p14-account="terminal.oxygen_melt_offgas_stored"'
    ) < html.index('data-p14-account="terminal.oxygen_mre_anode_stored"') < html.index(
        'data-p14-account="terminal.offgas"'
    ) < html.index('data-p14-account="process.cleaned_melt"')
    assert "#d95f02" in html  # Fe family from shared speciesColor().
    assert "#2b8cbe" in html  # O family from shared speciesColor().
    assert "kg-projected tier pending — backend kg projection not emitted" in html
    assert state["interaction"] == {
        "handlerInstalled": True,
        "rowId": state["interaction"]["rowId"],
        "rowScrolled": 1,
        "rowFocused": 1,
        "rowTabIndex": "-1",
        "localIdRemoved": True,
    }
    assert state["interaction"]["rowId"].startswith("sec-p14-account-terminal-offgas-")


def test_p14_absent_final_state_and_provenance_stay_pending() -> None:
    html = _render_panel(_artifact({}))["html"]

    assert "Pending terminal inventory" in html
    assert "terminal.final_state is not emitted for this run." in html
    assert "feedstock-provenance tier pending (origin data not emitted for this run)." in html
    assert "0 mol" not in html
    assert "sec-p14-ribbon" not in html


def test_p14_present_provenance_surfaces_authority_but_not_derived_shares() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {"terminal.offgas": {"Na": 5.0}},
                "yield_disposition": {
                    "basis": "target_atom_equivalent",
                    "authoritative": False,
                    "diagnostic_only": True,
                    "status": "partial",
                    "source": "<untrusted source>",
                    "targets": {
                        "Fe": {
                            "denominator_target_equiv_mol": 2.0,
                            "yield_fraction": 0.5,
                        }
                    },
                },
            }
        )
    )["html"]

    assert "yield_disposition emitted · provenance schema check" in html
    assert "target_atom_equivalent" in html
    assert "authoritative</b> false" in html
    assert "diagnostic only</b> true" in html
    assert "status</b> partial" in html
    assert "&lt;untrusted source&gt;" in html
    assert "Pending origin-resolved shares" in html
    assert "No feedstock percentages are inferred" in html
    assert "50%" not in html
    assert "yield fraction" not in html.lower()


def test_p14_partial_numeric_map_does_not_derive_totals_or_widths() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "terminal.offgas": {"Fe": 5.0, "Si": "7"},
                    "process.cleaned_melt": {"MgO": 2.0},
                }
            }
        )
    )["html"]

    assert "pending · incomplete numeric account map" in html
    assert "account display sum pending" in html
    assert "width pending · malformed species map" in html
    assert "Fe</td><td class=\"num\">5 mol" in html
    assert "Si</td><td class=\"num\">non-numeric (string)" in html
    assert "Si 7 mol" not in html
    assert html.count('class="sec-p14-ribbon"') == 1


def test_p14_zero_and_signed_credit_accounts_are_not_trace_or_ribbons() -> None:
    html = _render_panel(
        _artifact(
            {
                "final_state": {
                    "reservoir.reagent.Na": {"Na": -8.0},
                    "process.metal_phase": {},
                    "terminal.offgas": {"Na": 0.0},
                }
            }
        )
    )["html"]

    assert "-8 mol · viewer display sum" in html
    assert "signed reservoir credit balance · no Sankey width" in html
    assert html.count("emitted empty / zero inventory · no ribbon") == 2
    assert "trace inventory" not in html
    assert 'class="sec-p14-ribbon"' not in html
    assert "Signed reservoir credit balances are included in the displayed Σ" in html
