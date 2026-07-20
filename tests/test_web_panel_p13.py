from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"


def _run_panel(
    artifact: Any,
    update_indexes: list[int] | None = None,
    *,
    dom_mode: str = "normal",
) -> dict:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const labelsSource = fs.readFileSync(process.argv[2], "utf8");
const panelSource = fs.readFileSync(process.argv[3], "utf8");
const artifact = JSON.parse(process.argv[4]);
const indexes = JSON.parse(process.argv[5]);
const domMode = process.argv[6];
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
const context = { console };
if (domMode !== "none") {
  context.document = {
    querySelector(selector) {
      if (domMode === "no-live" && selector === "#p13-status-strip-live") return null;
      return {
        "#p13-status-strip-live": live,
        "#sec-p13-status-strip": panelSection,
        ".stepper": stepper
      }[selector] || null;
    }
  };
}
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
            dom_mode,
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _class_inner(markup: str, class_name: str) -> str:
    match = re.search(
        rf'<[^>]+class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</[^>]+>',
        markup,
        flags=re.DOTALL,
    )
    assert match, f"missing rendered class {class_name}"
    return match.group(1).strip()


def _tile(markup: str, tile_name: str, next_tile_name: str | None = None) -> str:
    fragment = markup.split(f'class="sec-p13-tile sec-p13-{tile_name}"', 1)[1]
    if next_tile_name:
        fragment = fragment.split(
            f'class="sec-p13-tile sec-p13-{next_tile_name}"', 1
        )[0]
    return fragment


def _stylesheet_selectors(css: str) -> list[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    selectors: list[str] = []
    prelude: list[str] = []
    for char in css:
        if char == "{":
            header = "".join(prelude).strip()
            if header and not header.startswith("@"):
                selectors.extend(part.strip() for part in header.split(","))
            prelude = []
        elif char == "}":
            prelude = []
        else:
            prelude.append(char)
    return selectors


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
            "status": "ok",
            "source": "backend redox ledger",
            "reference": "IW buffer",
            "authoritative": False,
            "diagnostic_only": True,
            "extrapolation": True,
            "high_uncertainty": True,
            "native_fe_saturation_event": {
                "native_fe_event": "deferred_not_liquid_for_redox",
                "native_fe_event_status": "deferred",
                "native_fe_event_reason": "deferred_not_liquid_for_redox",
            },
        },
        "regime": "viscous",
        "Kn": 0.0038201,
        "transport_formula_id": "bernoulli_swept_v2",
        "mre_ellingham_ladder_diagnostic": {
            "declared_rung_V": 4.75,
            "certification": "diagnostic_uncertified",
            "authority": "authoritative_ellingham_graph_with_static_fallback",
            "derived_Ed_V": {"NiO": 4.7521, "FeO": 1.231},
            "species": {
                "NiO": {
                    "voltage_authority": "ellingham_graph",
                    "voltage_authoritative": True,
                    "status": "ok",
                },
                "FeO": {
                    "voltage_authority": "ellingham_graph",
                    "voltage_authoritative": True,
                    "status": "ok",
                },
            },
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
    assert "IW buffer log fO₂ -71.6" in html
    assert "ΔIW not emitted" in html
    assert "ΔIW -71.6" not in html
    assert "Fe³⁺/ΣFe" in html
    assert "Native Fe 0 · event status: deferred" in html
    assert "event reason: deferred_not_liquid_for_redox" in html
    assert "backend redox ledger" in html
    assert "Reference</dt><dd>IW buffer</dd>" in html
    assert "Redox diagnostic status" in html
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
    assert "voltage authority: ellingham_graph" in html
    assert "voltage authoritative: true" in html
    assert "voltage status: ok" in html
    assert "MRE activity: not emitted" in html
    flow = _tile(update, "flow", "mre")
    assert _class_inner(flow, "sec-p13-headline") == "viscous / swept"


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
    assert _class_inner(html, "sec-p13-activity") == "MRE activity: not emitted"
    assert "MRE active" not in html
    assert "MRE inactive" not in html


def test_p13_flow_has_no_authority_chip_and_untrusted_values_are_escaped() -> None:
    summary = _present_summary()
    summary["campaign"] = '<img src=x onerror="boom">'
    summary["regime"] = "free_molecular"
    summary["transport_formula_id"] = '<script>alert("flow")</script>'
    result = _run_panel({"timesteps": [{"summary": summary}]})
    html = result["rendered"]
    flow = _tile(html, "flow", "mre")

    assert _class_inner(flow, "sec-p13-headline") == "ballistic"
    assert "authority" not in flow.lower()
    assert "sec-p13-chip" not in flow
    assert "&lt;script&gt;alert(&quot;flow&quot;)&lt;/script&gt;" in flow
    assert "<script>" not in flow
    assert "&lt;img src=x onerror=&quot;boom&quot;&gt;" in html
    assert "<img" not in html
    assert "MRE activity: not emitted" in html


def test_p13_iw_is_absolute_and_delta_iw_stays_pending_without_emitted_offset() -> None:
    summary = _present_summary()
    summary["fe_redox_split"] = {
        "fO2_log": -10.0,
        "iw_log": -80.0,
        "native_fe_frac": 0.25,
        "status": "ok",
    }
    html = _run_panel({"timesteps": [{"summary": summary}]})["rendered"]
    redox = _tile(html, "redox", "flow")

    assert "IW buffer log fO₂ -80" in redox
    assert "ΔIW not emitted" in redox
    assert "ΔIW -80" not in redox
    assert "ΔIW 70" not in redox
    assert "Native Fe 0.25 · event status not emitted" in redox
    assert "Native Fe 0.25 · ok" not in redox


def test_p13_non_authoritative_voltage_keeps_species_flags() -> None:
    summary = _present_summary()
    diagnostic = summary["mre_ellingham_ladder_diagnostic"]
    diagnostic["derived_Ed_V"] = {"FeO": 1.234}
    diagnostic.pop("species")
    diagnostic["non_authoritative_voltage_by_oxide"] = {
        "FeO": {
            "authority": "ellingham_fallback",
            "authoritative": False,
            "status": "ellingham_fallback:ellingham_nonpositive_refused:voltage",
        }
    }
    html = _run_panel({"timesteps": [{"summary": summary}]})["rendered"]

    assert "FeO: 1.234 V" in html
    assert "voltage authority: ellingham_fallback" in html
    assert "voltage authoritative: false" in html
    assert "ellingham_fallback:ellingham_nonpositive_refused:voltage" in html


def test_p13_partial_subtrees_withhold_absent_numbers_and_unqualified_voltage() -> None:
    summary = {
        "campaign": "C5",
        "fe_redox_split": {
            "fO2_log": -10.0,
            "iw_log": -80.0,
            "status": "ok",
            "authoritative": False,
        },
        "mre_ellingham_ladder_diagnostic": {
            "certification": "diagnostic_uncertified",
            "authority": "mixed_root_authority",
            "derived_Ed_V": {"FeO": 70.0},
        },
    }
    html = _run_panel({"timesteps": [{"summary": summary}]})["rendered"]
    redox = _tile(html, "redox", "flow")

    assert "IW buffer log fO₂ -80" in redox
    assert "ΔIW not emitted" in redox
    assert "Fe³⁺/ΣFe</dt><dd>not emitted" in redox
    assert "Ferric fraction</dt><dd>not emitted" in redox
    assert "Ferrous fraction</dt><dd>not emitted" in redox
    assert "Native Fe fraction</dt><dd>not emitted" in redox
    assert "Native Fe state not emitted" in redox
    assert "Ladder not emitted" in html
    assert "FeO: voltage withheld; per-species authority/status not emitted" in html
    assert "70 V" not in html
    assert "ΔIW 70" not in html


@pytest.mark.parametrize(
    ("regime", "expected"),
    [
        ("free_molecular", "ballistic"),
        ("transitional", "transitional"),
        ("viscous", "viscous / swept"),
        ("", "regime not emitted"),
    ],
)
def test_p13_flow_headline_maps_emitted_regime_exactly(
    regime: str, expected: str
) -> None:
    summary = _present_summary()
    summary["regime"] = regime
    html = _run_panel({"timesteps": [{"summary": summary}]})["rendered"]
    flow = _tile(html, "flow", "mre")

    assert _class_inner(flow, "sec-p13-headline") == expected


def test_p13_successful_ladder_cannot_imply_mre_activity() -> None:
    html = _run_panel({"timesteps": [{"summary": _present_summary()}]})["rendered"]

    assert "Ladder 4.75 V" in html
    assert _class_inner(html, "sec-p13-activity") == "MRE activity: not emitted"


def test_p13_all_artifact_text_routes_escape_exactly_once() -> None:
    hostile = '<img src=x onerror="boom">'
    encoded = "&lt;img src=x onerror=&quot;boom&quot;&gt;"
    summary = _present_summary()
    redox = summary["fe_redox_split"]
    for key in ("source", "reference", "skip_reason", "refusal_context", "authority"):
        redox[key] = hostile
    summary["regime"] = hostile
    diagnostic = summary["mre_ellingham_ladder_diagnostic"]
    for key in ("authority", "status", "source", "reference", "skip_reason", "refusal_context"):
        diagnostic[key] = hostile
    diagnostic["derived_Ed_V"] = {hostile: 1.2}
    diagnostic["species"] = {
        hostile: {
            "voltage_authority": hostile,
            "voltage_authoritative": False,
            "status": hostile,
        }
    }
    html = _run_panel({"timesteps": [{"summary": summary}]})["rendered"]

    assert encoded in html
    assert "<img" not in html
    assert "&amp;lt;img" not in html
    assert f"{encoded}: 1.2 V" in html


@pytest.mark.parametrize(
    ("artifact", "index"),
    [
        (None, 0),
        ({}, 0),
        ("malformed", 0),
        ({"timesteps": None}, 0),
        ({"timesteps": []}, 0),
        ({"timesteps": [{"summary": "malformed"}]}, 0),
        ({"timesteps": [{"summary": _present_summary()}]}, -1),
        ({"timesteps": [{"summary": _present_summary()}]}, 99),
    ],
)
def test_p13_malformed_artifacts_and_indexes_render_honest_pending(
    artifact: Any, index: int
) -> None:
    result = _run_panel(artifact, [index])

    assert "Timestep status not emitted" in result["rendered"] or index != 0
    assert "Timestep status not emitted" in result["updates"][0]


@pytest.mark.parametrize("dom_mode", ["none", "no-live"])
def test_p13_on_timestep_tolerates_missing_dom_targets(dom_mode: str) -> None:
    result = _run_panel(None, [0], dom_mode=dom_mode)

    assert result["updates"] == [""]


def test_p13_stylesheet_selectors_stay_panel_scoped() -> None:
    css = (VIEWER / "panels" / "p13-status-strip.css").read_text()
    selectors = _stylesheet_selectors(css)
    root = ".sec-p13-status-strip"
    rooted_forms = tuple(
        f"{root}{suffix}"
        for suffix in (" ", ".", ":", "#", "[", ">", "+", "~")
    )

    assert selectors
    assert all(selector == root or selector.startswith(rooted_forms) for selector in selectors)
