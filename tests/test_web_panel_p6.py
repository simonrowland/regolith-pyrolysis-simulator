from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p6-mre.js"
_AUTO_ROWS = object()


def _render(artifact: object, rows: object = _AUTO_ROWS) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const rows = process.argv[5] === ""
  ? (artifact && Array.isArray(artifact.timesteps)
      ? artifact.timesteps.map((step) => step && step.summary)
      : [])
  : JSON.parse(process.argv[5]);
const panel = context.ReportPanels.find((entry) => entry.id === "sec-p6-mre");
process.stdout.write(panel.render(artifact, rows, [], {}));
"""
    rows_json = "" if rows is _AUTO_ROWS else json.dumps(rows)
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL), json.dumps(artifact), rows_json],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _species_color(species: str) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = {};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
process.stdout.write(context.ReportLabels.speciesColor(JSON.parse(process.argv[3])));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS), json.dumps(species)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _between(rendered: str, start: str, end: str) -> str:
    assert start in rendered
    remainder = rendered.split(start, 1)[1]
    assert end in remainder
    return remainder.split(end, 1)[0]


def _value_for_label(rendered: str, label: str) -> str:
    match = re.search(
        rf"<span>{re.escape(label)}</span><b>(.*?)</b>", rendered, re.DOTALL
    )
    assert match is not None, f"missing rendered label/value row: {label}"
    return match.group(1)


def _numeric_measurements(rendered: str) -> list[str]:
    measurements = []
    for fragment in re.findall(r"<b(?: [^>]*)?>(.*?)</b>", rendered, re.DOTALL):
        text = re.sub(r"<[^>]+>", "", fragment)
        numeric_measurement = (
            r"(?:^|\s)[+-]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
            r"(?:e[+-]?\d+)?\s*(?:V|A|kg/h|mol)\b"
        )
        if re.search(numeric_measurement, text, re.IGNORECASE):
            measurements.append(text)
    return measurements


def _ladder() -> dict:
    return {
        "schema": "c5_ellingham_ladder_diagnostic_v1",
        "certification": "diagnostic_uncertified",
        "authority": "authoritative_ellingham_graph_with_static_fallback",
        "activity_basis": "gamma_x_single_cation_cleaned_melt_account",
        "temperature_C": 1225.0,
        "temperature_K": 1498.15,
        "pO2_bar": 0.05,
        "declared_rung_V": 0.39,
        "rung_species": ["FeO", "SiO2"],
        "derived_Ed_V": {"FeO": 1.006},
        "delta_vs_declared_rung_V": {"FeO": 0.616},
        "reordering": {
            "ordering_divergence_detected": False,
            "other_species_below_declared_rung": [],
            "derived_order_by_Ed": ["FeO", "SiO2"],
            "declared_order_by_static_voltage": ["FeO", "SiO2"],
        },
        "non_authoritative_voltage_by_oxide": {
            "MnO": {
                "authority": "ellingham_graph",
                "authoritative": False,
                "status": "diagnostic_reconstructed_mn_row_not_authoritative_for_mre",
                "static_declared_V": 1.42,
            }
        },
        "species": {
            "FeO": {
                "ellingham_species": "Fe",
                "static_declared_V": 0.91,
                "oxide_activity": 0.05,
                "oxide_activity_model": "gamma_x_single_cation",
                "inventory_present": True,
                "derived_Ed_V": 1.006,
                "delta_vs_declared_rung_V": 0.616,
                "delta_vs_static_declared_V": 0.096,
                "declared_after_held_rung": True,
                "voltage_authority": "ellingham_graph",
                "voltage_authoritative": True,
                "status": "ok",
            }
        },
    }


def _artifact(summary: dict, final_state: dict | None = None) -> dict:
    return {
        "timesteps": [{"hour": 42, "summary": summary}],
        "terminal": {"final_state": final_state or {}},
    }


def test_p6_renders_emitted_ladder_yield_o2_and_pending_direct_readouts() -> None:
    summary = {
        "mre_ellingham_ladder_diagnostic": _ladder(),
        "mre_uncertified_yield": {
            "FeO": {
                "source_species": "Fe2O3",
                "produced_species": "FeO",
                "produced_kg": 1.25,
                "produced_mol": 17.4,
                "certification": "uncertified_ferric_to_ferrous_reference",
                "reference_V": 0.65,
                "reference_status": "uncertified_heuristic_reference_not_raw_thermo",
                "reason": "Heuristic reference only",
            }
        },
    }
    html = _render(
        _artifact(summary, {"terminal.oxygen_mre_anode_stored": {"O2": 42.0}})
    )
    ladder_region = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<h3>Latest emitted uncertified yield evidence</h3>",
    )
    derived_region = _between(
        ladder_region,
        "<h4>Derived versus declared evidence</h4>",
        "<h4>Emitted reordering object</h4>",
    )
    yield_region = _between(
        html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-block sec-p6-o2">',
    )
    o2_region = _between(
        html,
        '<div class="sec-p6-block sec-p6-o2">',
        '<div class="sec-p6-direct"',
    )
    direct_region = _between(html, '<div class="sec-p6-direct"', "</section>")

    assert 'id="sec-p6-mre"' in html
    assert "diagnostic_uncertified" in ladder_region
    assert "authoritative_ellingham_graph_with_static_fallback" in ladder_region
    assert "SiO₂" in _value_for_label(ladder_region, "Held-rung species")
    assert "0.39 V" in _value_for_label(ladder_region, "Declared accounting rung")
    assert "1.006 V" in derived_region
    assert "1.25 kg" in _value_for_label(yield_region, "Produced amount · kg")
    assert "17.4 mol" in _value_for_label(yield_region, "Produced amount · mol")
    assert "O₂ stored · MRE anode" in o2_region
    assert "42 mol" in o2_region
    assert "recovered" not in o2_region.lower()
    assert "mol-native" in o2_region
    assert direct_region.count("Pending producer") == 3


def test_p6_absent_subtrees_have_separate_pending_states_without_zeroes() -> None:
    html = _render(_artifact({}))

    assert "Pending ladder evidence" in html
    assert "Pending uncertified yield" in html
    assert "Pending MRE-anode O₂" in html
    assert html.count("Pending producer") == 3
    assert _numeric_measurements(html) == []


def test_p6_surfaces_nested_authority_uncertainty_and_escapes_values() -> None:
    hostile_yield_key = "<svg onload=alert(document.domain)>"
    hostile_product = "<img src=x onerror=alert(document.domain)>"
    ladder = _ladder()
    ladder["non_authoritative_voltage_by_oxide"]["MnO"]["status"] = (
        "<script>authority breach</script>"
    )
    summary = {
        "mre_ellingham_ladder_diagnostic": ladder,
        "mre_uncertified_yield": {
            hostile_yield_key: {
                "source_species": "Fe2O3",
                "produced_species": hostile_product,
                "produced_kg": 0.2,
                "produced_mol": 2.8,
                "certification": "uncertified",
                "reference_V": 0.65,
                "reference_status": "provisional",
                "reason": "A & B",
                "authoritative": False,
                "diagnostic_only": True,
                "high_uncertainty": True,
                "extrapolation": "outside_reference_range",
                "status": "provisional",
                "basis": "per_tick",
                "source": "builtin",
                "reference": "heuristic",
                "skip_reason": "none",
            }
        },
    }
    html = _render(
        _artifact(summary, {"terminal.oxygen_mre_anode_stored": {"O2": 4.0}})
    )
    ladder_region = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<h3>Latest emitted uncertified yield evidence</h3>",
    )
    yield_region = _between(
        html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-block sec-p6-o2">',
    )

    assert "Voltage Authoritative" in ladder_region
    assert (
        '<b>Authoritative</b>false</span><span class="sec-p6-chip sec-p6-warn">'
        '<b>Status</b>&lt;script&gt;authority breach&lt;/script&gt;'
        in ladder_region
    )
    assert _value_for_label(yield_region, "Authoritative") == "false"
    assert _value_for_label(yield_region, "Diagnostic only") == "true"
    assert _value_for_label(yield_region, "High uncertainty") == "true"
    assert _value_for_label(yield_region, "Extrapolation") == "outside_reference_range"
    assert _value_for_label(yield_region, "Status") == "provisional"
    assert _value_for_label(yield_region, "Basis") == "per_tick"
    assert _value_for_label(yield_region, "Source") == "builtin"
    assert _value_for_label(yield_region, "Reference") == "heuristic"
    assert _value_for_label(yield_region, "Skip reason") == "none"
    assert _value_for_label(yield_region, "Reason") == "A &amp; B"
    assert "&amp;amp;" not in yield_region
    assert html_lib.escape(hostile_yield_key) in yield_region
    assert html_lib.escape(hostile_product) in _value_for_label(
        yield_region, "Produced species"
    )
    assert f"--species-color:{_species_color('FeO')}" in ladder_region
    assert "&lt;script&gt;authority breach&lt;/script&gt;" in ladder_region
    assert "<script>authority breach</script>" not in html
    assert hostile_yield_key not in html
    assert hostile_product not in html

    # Flip path: inverted uncertainty flags must render their emitted values,
    # not stay green via unscoped ladder-side "true" tokens.
    flip_html = _render(
        _artifact(
            {
                "mre_ellingham_ladder_diagnostic": _ladder(),
                "mre_uncertified_yield": {
                    "FeO": {
                        "source_species": "Fe2O3",
                        "produced_species": "FeO",
                        "produced_kg": 0.1,
                        "produced_mol": 1.0,
                        "certification": "uncertified",
                        "reference_V": 0.65,
                        "reference_status": "provisional",
                        "reason": "flip fixture",
                        "authoritative": True,
                        "diagnostic_only": False,
                        "high_uncertainty": False,
                    }
                },
            }
        )
    )
    flip_yield = _between(
        flip_html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-direct"',
    )
    assert _value_for_label(flip_yield, "Authoritative") == "true"
    assert _value_for_label(flip_yield, "Diagnostic only") == "false"
    assert _value_for_label(flip_yield, "High uncertainty") == "false"


def test_p6_partial_inputs_stay_pending_and_are_not_derived_or_substituted() -> None:
    ladder = {
        "schema": "partial",
        "certification": "diagnostic_uncertified",
        "authority": "read_only",
        "activity_basis": "cleaned_melt",
        "temperature_C": 1000.0,
        "pO2_bar": 0.01,
        "declared_rung_V": 1.1,
        "rung_species": ["FeO"],
        "derived_Ed_V": {"FeO": 1.5},
        "species": {},
    }
    summary = {
        "mre_ellingham_ladder_diagnostic": ladder,
        "mre_uncertified_yield": {
            "FeO": {
                "source_species": "Fe2O3",
                "produced_species": "FeO",
                "produced_mol": 1.0,
                "certification": "uncertified",
                "reference_V": 0.65,
                "reference_status": "provisional",
                "reason": "partial fixture",
            }
        },
    }
    html = _render(
        _artifact(summary, {"terminal.oxygen_melt_offgas_stored": {"O2": 99.0}})
    )
    ladder_region = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<h3>Latest emitted uncertified yield evidence</h3>",
    )
    yield_region = _between(
        html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-direct"',
    )
    derived_region = _between(
        ladder_region,
        "<h4>Derived versus declared evidence</h4>",
        "<h4>Emitted reordering object</h4>",
    )
    derived_row = _between(derived_region, "<tbody><tr>", "</tr>")

    assert "Delta vs declared · V" in ladder_region
    assert "1.5 V" in derived_row and "not emitted" in derived_row
    assert "0.4 V" not in derived_region
    assert "not emitted" in _value_for_label(ladder_region, "Temperature")
    assert "1273 K" not in ladder_region and "1,273 K" not in ladder_region
    assert "not emitted" in _value_for_label(yield_region, "Produced amount · kg")
    assert "0.07184 kg" not in yield_region
    assert "Pending reordering evidence" in ladder_region
    assert "Pending non-authoritative voltage entries" in ladder_region
    assert "Pending MRE-anode O₂" in yield_region
    assert "99 mol" not in yield_region
    assert html.count("Pending producer") == 3

    kg_only_html = _render(
        _artifact(
            {
                "mre_uncertified_yield": {
                    "FeO": {
                        "source_species": "Fe2O3",
                        "produced_species": "FeO",
                        "produced_kg": 0.07184,
                        "certification": "uncertified",
                        "reference_V": 0.65,
                        "reference_status": "provisional",
                        "reason": "inverse partial fixture",
                    }
                }
            }
        )
    )
    kg_only_yield = _between(
        kg_only_html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-direct"',
    )
    assert "not emitted" in _value_for_label(
        kg_only_yield, "Produced amount · mol"
    )
    assert "1 mol" not in kg_only_yield


def test_p6_render_handles_null_rows_summaries_and_malformed_terminal_account() -> None:
    null_html = _render(None, None)

    assert "Pending ladder evidence" in null_html
    assert "Pending uncertified yield" in null_html
    assert "Pending MRE-anode O₂" in null_html
    assert null_html.count("Pending producer") == 3

    malformed_html = _render(
        {
            "terminal": {
                "final_state": {"terminal.oxygen_mre_anode_stored": []}
            }
        },
        [None, "malformed summary", {}],
    )

    assert "Pending ladder evidence" in malformed_html
    assert "Pending uncertified yield" in malformed_html
    assert "Malformed MRE-anode O₂" in malformed_html
    assert _numeric_measurements(malformed_html) == []

    empty_ladder = _ladder()
    empty_ladder["rung_species"] = []
    zero_html = _render(
        _artifact(
            {"mre_ellingham_ladder_diagnostic": empty_ladder},
            {"terminal.oxygen_mre_anode_stored": {"O2": 0.0}},
        )
    )
    zero_ladder_region = _between(
        zero_html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<strong>Pending uncertified yield</strong>",
    )
    zero_o2_region = _between(
        zero_html,
        '<div class="sec-p6-block sec-p6-o2">',
        '<div class="sec-p6-direct"',
    )

    assert "empty emitted list" in _value_for_label(
        zero_ladder_region, "Held-rung species"
    )
    assert "0 mol" in zero_o2_region
    assert "Pending MRE-anode O₂" not in zero_html
    assert "Malformed MRE-anode O₂" not in zero_html
