from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p2-taps.js"
PANEL_CSS = ROOT / "web/report_viewer/panels/p2-taps.css"


def _render(
    artifact: dict | None,
    *,
    species_color_sentinel: str | None = None,
    spy_esc: bool = False,
) -> str | tuple[str, int]:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const speciesColorSentinel = JSON.parse(process.argv[5]);
const spyEsc = JSON.parse(process.argv[6]);
let escCalls = 0;
if (speciesColorSentinel !== null || spyEsc) {
  const base = context.ReportLabels;
  context.ReportLabels = Object.freeze({
    ...base,
    ...(speciesColorSentinel !== null
      ? { speciesColor: () => speciesColorSentinel }
      : {}),
    ...(spyEsc
      ? {
          esc: (value) => {
            escCalls += 1;
            return base.esc(value);
          },
        }
      : {}),
  });
}
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
if (!Array.isArray(context.ReportPanels) || context.ReportPanels.length !== 1) {
  throw new Error(`expected exactly one panel registration, got ${context.ReportPanels?.length}`);
}
if (context.ReportPanels[0].id !== "sec-p2-taps") {
  throw new Error(`wrong panel registration id: ${context.ReportPanels[0].id}`);
}
const artifact = JSON.parse(process.argv[4]);
const timesteps = Array.isArray(artifact?.timesteps) ? artifact.timesteps : [];
const rows = timesteps.map((timestep) => {
  if (!timestep || typeof timestep !== "object" || Array.isArray(timestep)) return {};
  const summary = timestep.summary;
  return summary && typeof summary === "object" && !Array.isArray(summary) ? summary : {};
});
const html = context.ReportPanels[0].render(artifact, rows, [], {});
if (spyEsc) {
  process.stdout.write(JSON.stringify({ html, escCalls }));
} else {
  process.stdout.write(html);
}
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(LABELS),
            str(PANEL),
            json.dumps(artifact),
            json.dumps(species_color_sentinel),
            json.dumps(spy_esc),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    if spy_esc:
        payload = json.loads(completed.stdout)
        return payload["html"], int(payload["escCalls"])
    return completed.stdout


def _between(html: str, start_marker: str, end_marker: str) -> str:
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def _tap_card(html: str, card_id: str) -> str:
    marker = f'<article class="card sec-p2-card" data-p2-card="{card_id}">'
    return _between(html, marker, "</article>")


def _species_map_region(card_html: str, heading: str) -> str:
    marker = f"<h3>{heading}</h3><div class=\"sec-p2-species-list\">"
    return _between(card_html, marker, "</div>")


def _interface_row(html: str, label: str) -> str:
    marker = f'<div class="sec-p2-kv"><span>{label}</span><b>'
    return _between(html, marker, "</div>")


def _container_notice(html: str, region: str) -> str:
    marker = (
        '<div class="pending sec-p2-inline-pending" '
        f'data-p2-container="{region}">'
    )
    return _between(html, marker, "</div>")


def _top_flags(html: str) -> str:
    return _between(
        html,
        '<div class="sec-p2-flags" data-p2-flags="stratification">',
        "</div>",
    )


def _subtitle(html: str) -> str:
    return _between(html, '<p class="sub">', "</p>")


def _artifact(*, stratification: dict | None, final_state: dict | None = None) -> dict:
    summary = {}
    if stratification is not None:
        summary["metal_phase_stratification"] = stratification
    return {
        "artifact_schema_version": "0.2.0",
        "header": {"run_id": "p2-test"},
        "timesteps": [{"hour": 12, "summary": summary}],
        "terminal": {"final_state": final_state or {}},
    }


def _present_stratification() -> dict:
    return {
        "status": "diagnostic_only_no_tap_gate",
        "mode": "stratify",
        "existing_extraction_behavior": "unchanged_diagnostic_only",
        "authoritative": False,
        "diagnostic_only": True,
        "extrapolation": True,
        "high_uncertainty": True,
        "temperature_K": 1673.15,
        "melt_density_kg_m3": 2700.0,
        "melt_density_tier": "fallback_basaltic_melt_constant_engine_density_unavailable",
        "melt_density_fallback_engaged": True,
        "provenance": {
            "account_state_source": "builtin-authored",
            "diagnostic_derivation": "builtin-committed",
        },
        "pools": {
            "float_layer": {
                "high_uncertainty": False,
                "species_mol": {"Al": 7.0, "Si": 1.0},
                "composition_wt_pct": {"Al": 88.25, "Si": 11.75},
                "density_kg_m3": 2240.0,
                "buoyancy": {
                    "verdict": "float",
                    "alloy_density_kg_m3": 2240.0,
                    "melt_density_kg_m3": 2700.0,
                    "delta_rho_kg_m3": -460.0,
                    "ambiguity_threshold_kg_m3": 150.0,
                    "melt_density_uncertainty_kg_m3": 135.0,
                    "alloy_density_uncertainty_kg_m3": 20.0,
                },
                "density_correlation_provenance": {
                    "Al": {
                        "source": "Assael density source",
                        "valid_range_K": [933.0, 1190.0],
                        "temperature_K": 1673.15,
                        "status": "extrapolated_above_valid_range",
                    }
                },
            },
            "bottom_pool": {
                "species_mol": {"Fe": 100.0, "Co": 2.0, "Ni": 1.0},
                "composition_wt_pct": {"Fe": 97.1, "Co": 1.9, "Ni": 1.0},
                "density_kg_m3": 7200.0,
                "buoyancy": {
                    "verdict": "sink",
                    "alloy_density_kg_m3": 7200.0,
                    "melt_density_kg_m3": 2700.0,
                    "delta_rho_kg_m3": 4500.0,
                    "ambiguity_threshold_kg_m3": 260.0,
                    "melt_density_uncertainty_kg_m3": 135.0,
                    "alloy_density_uncertainty_kg_m3": 230.0,
                },
                "density_correlation_provenance": {
                    "Fe": {
                        "source": "Assael iron source",
                        "valid_range_K": [1809.0, 2480.0],
                        "temperature_K": 1673.15,
                        "status": "extrapolated_below_valid_range",
                    }
                },
            },
        },
        "unclassified_staging_mol": {"Co": 0.4, "W": 0.1},
        "interface": {
            "area_m2": 0.2,
            "float_layer_mass_kg": 0.25,
            "equivalent_film_thickness_m": 0.0005,
            "coverage_reference_thickness_m": 0.001,
            "coverage_fraction_at_reference_thickness": 0.5,
            "aggressive_float_tap_assumption_load_bearing": True,
        },
    }


def test_p2_renders_two_product_inventories_contaminants_accounts_and_geometry() -> None:
    html = _render(_artifact(
        stratification=_present_stratification(),
        final_state={
            "process.metal_phase_float_layer": {"Al": 70.0, "Si": 10.0},
            "process.metal_phase_bottom_pool": {"Fe": 1000.0, "Co": 20.0, "Ni": 10.0},
        },
    ))
    upper = _tap_card(html, "float")
    bottom = _tap_card(html, "bottom")
    upper_pool = _species_map_region(upper, "Stratified pool · mol by species")
    upper_terminal = _species_map_region(upper, "Final-state process pool · mol by species")
    bottom_pool = _species_map_region(bottom, "Stratified pool · mol by species")
    bottom_terminal = _species_map_region(bottom, "Final-state process pool · mol by species")
    subtitle = _subtitle(html)

    assert "Metal-pool product inventory &amp; stratification" in html
    assert "Upper float-layer inventory" in upper
    assert "Bottom-pool inventory" in bottom
    # F5 (codex): scope drain wording to stratification accounts, not run-wide denial.
    assert "These stratification pool accounts do not represent a fired drain tap" in subtitle
    assert "no drain tap is simulated" not in html
    assert "Upper float tap" not in upper
    assert "Bottom pool tap" not in bottom
    assert 'Al</b> <span title="7 mol">7 mol</span>' in upper_pool
    assert 'Fe</b> <span title="100 mol">100 mol</span>' in bottom_pool
    assert "<b>Fe</b>" not in upper_pool
    assert "<b>Al</b>" not in bottom_pool
    assert 'Al</b> <span title="70 mol">70 mol</span>' in upper_terminal
    assert 'Fe</b> <span title="1000 mol">1,000 mol</span>' in bottom_terminal
    assert "<b>Fe</b>" not in upper_terminal
    assert "<b>Al</b>" not in bottom_terminal
    # F1: viewer ranking labeled as viewer-computed (not emitted ranking).
    assert "Viewer-computed top species: Fe 97.1 wt%" in bottom
    assert "largest emitted pool share" not in bottom
    assert 'Co contaminant</b> <span title="1.9 wt%">1.9 wt%</span>' in bottom
    assert 'Ni contaminant</b> <span title="1 wt%">1 wt%</span>' in bottom
    assert "Equivalent film thickness" in html and "5.00e-4 m" in html
    assert "Coverage fraction at reference thickness" in html and ">0.5<" in html
    assert "Frozen kg product-pool views pending producer attach" in html
    assert "literal terminal tap accounts" not in html
    # F10 (codex): present density rows keep kg/m³ units.
    assert "Pool density</span><b><span title=\"7200 kg/m³\">7,200 kg/m³</span></b>" in bottom
    assert "Alloy density</span><b><span title=\"7200 kg/m³\">7,200 kg/m³</span></b>" in bottom
    assert "Density contrast</span><b><span title=\"4500 kg/m³\">4,500 kg/m³</span></b>" in bottom


def test_p2_does_not_deny_run_wide_drain_tap_when_account_present() -> None:
    """F5: valid terminal.drain_tap_material must not be denied by panel subtitle."""
    html = _render(_artifact(
        stratification=_present_stratification(),
        final_state={
            "process.metal_phase_bottom_pool": {"Fe": 3.0},
            "terminal.drain_tap_material": {"Fe": 3.0},
        },
    ))
    subtitle = _subtitle(html)
    assert "These stratification pool accounts do not represent a fired drain tap" in subtitle
    assert "no drain tap is simulated" not in subtitle
    assert "no drain tap is simulated" not in html


def test_p2_absent_stratification_stays_pending_without_hiding_terminal_mol() -> None:
    html = _render(_artifact(
        stratification=None,
        final_state={"process.metal_phase_bottom_pool": {"Fe": 3.0}},
    ))
    state = _between(
        html,
        '<div class="sec-p2-state" data-p2-region="state">',
        '<div class="sec-p2-grid">',
    )
    upper_grade = _between(
        _tap_card(html, "float"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    bottom_grade = _between(
        _tap_card(html, "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )

    assert "Pending metal-phase stratification" in html
    assert "Elemental pool composition pending" in html
    assert "Pool mol amounts are not converted into wt%" in html
    assert 'Fe</b> <span title="3 mol">3 mol</span>' in html
    assert "Emitted temperature</span><b>not emitted</b>" in state
    assert "Emitted melt density</span><b>not emitted</b>" in state
    assert " wt%</span>" not in upper_grade
    assert " wt%</span>" not in bottom_grade

    partial = _present_stratification()
    partial.pop("temperature_K")
    partial.pop("melt_density_kg_m3")
    partial_html = _render(_artifact(stratification=partial))
    partial_state = _between(
        partial_html,
        '<div class="sec-p2-state" data-p2-region="state">',
        '<div class="sec-p2-grid">',
    )
    assert "Emitted temperature</span><b>not emitted</b>" in partial_state
    assert "Emitted melt density</span><b>not emitted</b>" in partial_state
    assert "1673.15 K" not in partial_state
    assert "2,700 kg/m³" not in partial_state


def test_p2_partial_composition_does_not_derive_wt_pct_from_species_mol() -> None:
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"].pop("composition_wt_pct")
    stratification["pools"]["bottom_pool"]["species_mol"] = {"Fe": 2.0, "Co": 1.0}

    html = _render(_artifact(stratification=stratification))
    bottom_grade = _between(
        _tap_card(html, "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )

    assert "Elemental pool composition by weight was not emitted" in bottom_grade
    assert "Pool mol amounts are not converted into wt%" in bottom_grade
    assert " wt%</span>" not in bottom_grade
    assert 'Fe</b> <span title="2 mol">2 mol</span>' in _tap_card(html, "bottom")
    assert 'Co</b> <span title="1 mol">1 mol</span>' in _tap_card(html, "bottom")


def test_p2_partial_interface_does_not_derive_geometry() -> None:
    cases = (
        ("area_m2", "Interface area", "0.2232 m²"),
        ("float_layer_mass_kg", "Float-layer mass", "0.224 kg"),
        ("equivalent_film_thickness_m", "Equivalent film thickness", "5.58e-4 m"),
        ("coverage_reference_thickness_m", "Coverage reference thickness", "0.001 m"),
        ("coverage_fraction_at_reference_thickness", "Coverage fraction at reference thickness", ">0.5<"),
    )

    for field, label, derivable_value in cases:
        stratification = _present_stratification()
        stratification["interface"].pop(field)
        html = _render(_artifact(stratification=stratification))
        row = _interface_row(html, label)

        assert f"{label}</span><b>not emitted</b>" in row
        assert derivable_value not in row
        assert "</span><b><span" not in row


def test_p2_surfaces_diagnostic_authority_and_load_bearing_flags() -> None:
    html = _render(_artifact(stratification=_present_stratification()))
    top_flags = _top_flags(html)
    pool_flags = _between(
        _tap_card(html, "float"),
        '<div class="sec-p2-flags" data-p2-flags="pool">',
        "</div>",
    )

    assert "Diagnostic only · no tap gate" in html
    assert "Extraction behavior unchanged · diagnostic only" in html
    assert "Fallback basaltic-melt constant · engine density unavailable" in html
    assert "Melt-density fallback engaged:</b> true" in top_flags
    assert "Aggressive float-tap assumption: load-bearing" in html
    assert "Aggressive float-tap assumption</span><b>load-bearing" in html
    assert "Extrapolated below valid range" in html
    assert "Assael iron source" in html
    # F7 (codex): bind authority chips to emitted values in the top-flag region.
    assert "Authoritative:</b> false" in top_flags
    assert "Diagnostic only:</b> true" in top_flags
    assert "Extrapolation:</b> true" in top_flags
    assert "High uncertainty:</b> true" in top_flags
    assert "Account-state source:</b> builtin-authored" in top_flags
    assert "Diagnostic derivation:</b> builtin-committed" in top_flags
    assert "High uncertainty:</b> false" in pool_flags

    empty_status = _present_stratification()
    empty_status["status"] = ""
    empty_status_flags = _top_flags(_render(_artifact(stratification=empty_status)))
    assert "Diagnostic status:</b> empty" in empty_status_flags

    malformed_status = _present_stratification()
    malformed_status["status"] = {}
    malformed_status_flags = _top_flags(_render(_artifact(stratification=malformed_status)))
    assert "Diagnostic status:</b> malformed" in malformed_status_flags

    missing_assumption = _present_stratification()
    missing_assumption["interface"].pop("aggressive_float_tap_assumption_load_bearing")
    missing_assumption_html = _render(_artifact(stratification=missing_assumption))
    assert "Aggressive float-tap assumption: not emitted" in _tap_card(
        missing_assumption_html,
        "float",
    )
    assert "Aggressive float-tap assumption</span><b>not emitted</b>" in _interface_row(
        missing_assumption_html,
        "Aggressive float-tap assumption",
    )

    malformed_assumption = _present_stratification()
    malformed_assumption["interface"]["aggressive_float_tap_assumption_load_bearing"] = 0
    malformed_assumption_html = _render(_artifact(stratification=malformed_assumption))
    assert "Aggressive float-tap assumption: malformed" in _tap_card(
        malformed_assumption_html,
        "float",
    )
    assert "Aggressive float-tap assumption</span><b>malformed</b>" in _interface_row(
        malformed_assumption_html,
        "Aggressive float-tap assumption",
    )

    # F6 (gk): explicit false is not load-bearing (polarity guard).
    not_bearing = _present_stratification()
    not_bearing["interface"]["aggressive_float_tap_assumption_load_bearing"] = False
    not_bearing_html = _render(_artifact(stratification=not_bearing))
    assert "Aggressive float-tap assumption: not load-bearing" in _tap_card(
        not_bearing_html,
        "float",
    )
    assumption_row = _interface_row(not_bearing_html, "Aggressive float-tap assumption")
    assert "Aggressive float-tap assumption</span><b>not load-bearing</b>" in assumption_row
    assert re.search(r"<b>load-bearing</b>", assumption_row) is None


def test_p2_absent_top_diagnostic_fields_are_not_emitted_not_false() -> None:
    """F7 (codex/gk): omitted top fields must render not emitted, never false/0."""
    partial = {
        "status": "diagnostic_only_no_tap_gate",
        "pools": {
            "float_layer": {"species_mol": {"Al": 1.0}},
            "bottom_pool": {"species_mol": {"Fe": 1.0}},
        },
    }
    flags = _top_flags(_render(_artifact(stratification=partial)))
    for label in (
        "Mode",
        "Extraction behavior",
        "Melt-density tier",
        "Melt-density fallback engaged",
        "Account-state source",
        "Diagnostic derivation",
    ):
        assert f"{label}:</b> not emitted" in flags
        assert f"{label}:</b> false" not in flags
        assert f"{label}:</b> true" not in flags
        assert f"{label}:</b> 0" not in flags
    # Status was supplied — must keep emitted value, not collapse.
    assert "Diagnostic status:</b> Diagnostic only · no tap gate" in flags


def test_p2_malformed_authority_and_status_types_are_not_claims() -> None:
    """F2: wrong-typed status/authority/source must render malformed, not coerced claims."""
    stratification = _present_stratification()
    stratification["status"] = False
    stratification["mode"] = True
    stratification["melt_density_fallback_engaged"] = "false"
    stratification["authoritative"] = "true"
    stratification["diagnostic_only"] = "false"
    stratification["pools"]["float_layer"]["density_correlation_provenance"] = {
        "Al": {
            "source": 0,
            "valid_range_K": [933.0, 1190.0],
            "temperature_K": 1673.15,
            "status": "extrapolated_above_valid_range",
        }
    }
    html = _render(_artifact(stratification=stratification))
    flags = _top_flags(html)
    float_card = _tap_card(html, "float")
    provenance = _between(
        float_card,
        "<h3>Density-correlation provenance</h3>",
        '<div class="sec-p2-flags" data-p2-flags="pool">',
    )

    assert "Diagnostic status:</b> malformed" in flags
    assert "Mode:</b> malformed" in flags
    assert "Melt-density fallback engaged:</b> malformed" in flags
    assert "Authoritative:</b> malformed" in flags
    assert "Diagnostic only:</b> malformed" in flags
    assert "Diagnostic status:</b> false" not in flags
    assert "Mode:</b> true" not in flags
    assert "Melt-density fallback engaged:</b> false" not in flags
    assert "Authoritative:</b> true" not in flags
    assert "Diagnostic only:</b> false" not in flags
    assert ">malformed<" in provenance
    assert re.search(r">0<", provenance) is None

    null_source = _present_stratification()
    null_source["pools"]["float_layer"]["density_correlation_provenance"] = {
        "Al": {
            "source": None,
            "valid_range_K": [933.0, 1190.0],
            "temperature_K": 1673.15,
            "status": "within_valid_range",
        }
    }
    null_prov = _between(
        _tap_card(_render(_artifact(stratification=null_source)), "float"),
        "<h3>Density-correlation provenance</h3>",
        '<div class="sec-p2-flags" data-p2-flags="pool">',
    )
    assert "malformed" in null_prov
    assert "source not emitted" not in null_prov


def test_p2_distinguishes_grade_absent_empty_malformed_and_zero_states() -> None:
    present_html = _render(_artifact(stratification=_present_stratification()))
    present_contaminants = _between(
        _tap_card(present_html, "float"),
        '<div class="sec-p2-contaminants"',
        '<div class="sec-p2-kv">',
    )
    assert "Co not present in emitted composition" in present_contaminants
    assert "Ni not present in emitted composition" in present_contaminants
    assert "composition shares not emitted" not in present_contaminants

    absent = _present_stratification()
    absent["pools"]["bottom_pool"].pop("composition_wt_pct")
    absent_grade = _between(
        _tap_card(_render(_artifact(stratification=absent)), "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    assert "Elemental pool composition by weight was not emitted" in absent_grade
    assert "Co and Ni composition shares not emitted" in absent_grade

    empty = _present_stratification()
    empty["pools"]["bottom_pool"]["composition_wt_pct"] = {}
    empty_grade = _between(
        _tap_card(_render(_artifact(stratification=empty)), "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    assert "No species present in emitted composition" in empty_grade
    assert "Co not present in emitted composition" in empty_grade

    malformed = _present_stratification()
    malformed["pools"]["bottom_pool"]["composition_wt_pct"] = []
    malformed_grade = _between(
        _tap_card(_render(_artifact(stratification=malformed)), "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    assert "Emitted elemental pool composition by weight is not a species map" in malformed_grade
    assert "composition shares unavailable · emitted composition malformed" in malformed_grade

    mixed_malformed = _present_stratification()
    mixed_malformed["pools"]["bottom_pool"]["composition_wt_pct"] = {
        "Fe": "bad",
        "Co": 1.0,
    }
    mixed_malformed_card = _tap_card(
        _render(_artifact(stratification=mixed_malformed)),
        "bottom",
    )
    assert "Elemental pool composition malformed" in mixed_malformed_card
    assert "Viewer-computed top species" not in mixed_malformed_card
    assert "largest emitted pool share" not in mixed_malformed_card

    zero = _present_stratification()
    zero["pools"]["bottom_pool"]["composition_wt_pct"] = {"Co": 0.0}
    zero_card = _tap_card(_render(_artifact(stratification=zero)), "bottom")
    zero_grade = _between(
        zero_card,
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    assert "Elemental pool composition · no positive shares" in zero_card
    assert "<b>Co</b> emitted 0 wt% · not a positive contaminant" in zero_grade
    assert "Co contaminant</b>" not in zero_grade
    assert "Viewer-computed top species" not in zero_grade
    assert "Ni not present in emitted composition" in zero_grade

    # F4: over-100 and negative shares are malformed; exact 100 remains data.
    over = _present_stratification()
    over["pools"]["bottom_pool"]["composition_wt_pct"] = {"Fe": 120.0}
    over_card = _tap_card(_render(_artifact(stratification=over)), "bottom")
    over_grade = _between(over_card, '<div class="sec-p2-grade"', '<div class="sec-p2-kv">')
    assert "Elemental pool composition malformed" in over_card
    assert "composition value malformed" in over_grade
    assert "Viewer-computed top species" not in over_card
    assert "120 wt%" not in over_grade

    exact_100 = _present_stratification()
    exact_100["pools"]["bottom_pool"]["composition_wt_pct"] = {"Fe": 100.0}
    exact_card = _tap_card(_render(_artifact(stratification=exact_100)), "bottom")
    assert "Viewer-computed top species: Fe 100 wt%" in exact_card
    assert 'Fe</b> <span title="100 wt%">100 wt%</span>' in exact_card

    negative = _present_stratification()
    negative["pools"]["bottom_pool"]["composition_wt_pct"] = {"Fe": -1.0}
    negative_card = _tap_card(_render(_artifact(stratification=negative)), "bottom")
    negative_grade = _between(
        negative_card, '<div class="sec-p2-grade"', '<div class="sec-p2-kv">'
    )
    assert "Elemental pool composition malformed" in negative_card
    assert "composition value malformed" in negative_grade
    assert "-1 wt%" not in negative_grade


def test_p2_negative_mol_inventories_render_as_malformed() -> None:
    """F3: negative mol amounts are malformed inventory, not measured chips."""
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"]["species_mol"] = {"Fe": -1.0}
    stratification["unclassified_staging_mol"] = {"Co": -0.4}
    html = _render(_artifact(
        stratification=stratification,
        final_state={"process.metal_phase_bottom_pool": {"Fe": -2.0}},
    ))
    bottom = _tap_card(html, "bottom")
    pool = _species_map_region(bottom, "Stratified pool · mol by species")
    terminal = _species_map_region(bottom, "Final-state process pool · mol by species")
    unclassified = _between(
        html,
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "amount malformed" in pool
    assert "amount malformed" in terminal
    assert "amount malformed" in unclassified
    assert 'title="-1 mol"' not in pool
    assert 'title="-2 mol"' not in terminal
    assert 'title="-0.4 mol"' not in unclassified
    # Exact zero remains distinct data.
    zero = _present_stratification()
    zero["pools"]["bottom_pool"]["species_mol"] = {"Fe": 0.0}
    zero_pool = _species_map_region(
        _tap_card(_render(_artifact(stratification=zero)), "bottom"),
        "Stratified pool · mol by species",
    )
    assert 'Fe</b> <span title="0 mol">0 mol</span>' in zero_pool


def test_p2_renders_unclassified_staging_as_a_distinct_literal_bin() -> None:
    present_html = _render(_artifact(stratification=_present_stratification()))
    present = _between(
        present_html,
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "Unclassified metal staging · mol by species" in present
    assert 'Co</b> <span title="0.4 mol">0.4 mol</span>' in present
    assert 'W</b> <span title="0.1 mol">0.1 mol</span>' in present
    assert 'title="0.4 mol"' not in _tap_card(present_html, "bottom")

    absent = _present_stratification()
    absent.pop("unclassified_staging_mol")
    absent_region = _between(
        _render(_artifact(stratification=absent)),
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "Unclassified metal staging mol map not emitted" in absent_region

    empty = _present_stratification()
    empty["unclassified_staging_mol"] = {}
    empty_region = _between(
        _render(_artifact(stratification=empty)),
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "No unclassified species present in emitted map" in empty_region

    malformed = _present_stratification()
    malformed["unclassified_staging_mol"] = []
    malformed_region = _between(
        _render(_artifact(stratification=malformed)),
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "Unclassified metal staging mol map is malformed" in malformed_region

    zero = _present_stratification()
    zero["unclassified_staging_mol"] = {"Co": 0.0}
    zero_region = _between(
        _render(_artifact(stratification=zero)),
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert 'Co</b> <span title="0 mol">0 mol</span>' in zero_region


def test_p2_legacy_metal_phase_does_not_leak_into_float_or_bottom_cards() -> None:
    """F9 (gk): process.metal_phase stays out of float/bottom final-state regions."""
    html = _render(_artifact(
        stratification=_present_stratification(),
        final_state={
            "process.metal_phase": {"W": 99.0, "Cu": 11.0},
            "process.metal_phase_float_layer": {"Al": 1.0},
            "process.metal_phase_bottom_pool": {"Fe": 2.0},
        },
    ))
    upper_terminal = _species_map_region(
        _tap_card(html, "float"),
        "Final-state process pool · mol by species",
    )
    bottom_terminal = _species_map_region(
        _tap_card(html, "bottom"),
        "Final-state process pool · mol by species",
    )
    assert 'Al</b> <span title="1 mol">1 mol</span>' in upper_terminal
    assert 'Fe</b> <span title="2 mol">2 mol</span>' in bottom_terminal
    assert "<b>W</b>" not in upper_terminal
    assert "<b>Cu</b>" not in upper_terminal
    assert "<b>W</b>" not in bottom_terminal
    assert "<b>Cu</b>" not in bottom_terminal
    assert 'title="99 mol"' not in upper_terminal
    assert 'title="99 mol"' not in bottom_terminal


def test_p2_newest_malformed_stratification_does_not_fall_back_to_older_map() -> None:
    artifact = _artifact(stratification=None)
    artifact["timesteps"] = [
        {
            "hour": 11,
            "summary": {"metal_phase_stratification": _present_stratification()},
        },
        {
            "hour": 12,
            "summary": {"metal_phase_stratification": []},
        },
    ]

    html = _render(artifact)
    assert 'data-p2-container="stratification"' in html
    malformed = _between(
        html,
        '<div class="pending sec-p2-panel-pending" '
        'data-p2-container="stratification">',
        "</div>",
    )

    assert "Latest emitted stratification (hour 12)" in html
    assert "Malformed metal-phase stratification" in malformed
    assert "Older timestep reports are not substituted" in malformed
    assert "1673.15 K" not in html
    assert "Viewer-computed top species: Al 88.25 wt%" not in html
    assert "Al 88.25 wt% · largest emitted pool share" not in html


def test_p2_distinguishes_enclosing_container_states() -> None:
    cases = (
        ("absent", "Not emitted", "was not emitted"),
        ("empty", "Empty", "map is empty"),
        ("malformed", "Malformed", "is not a map"),
        ("zero", "Malformed", "is not a map"),
    )

    for state, heading, detail in cases:
        pools = _present_stratification()
        if state == "absent":
            pools.pop("pools")
        elif state == "empty":
            pools["pools"] = {}
        elif state == "malformed":
            pools["pools"] = []
        else:
            pools["pools"] = 0
        pools_html = _render(_artifact(stratification=pools))
        pools_notice = _container_notice(pools_html, "pools")
        assert f"<strong>{heading}</strong>" in pools_notice
        assert detail in pools_notice
        if state in {"malformed", "zero"}:
            # F5 (gk): malformed parent pools must not claim child inventory not emitted.
            float_card = _tap_card(pools_html, "float")
            bottom_card = _tap_card(pools_html, "bottom")
            assert "Stratification pools container malformed — child inventory unavailable" in float_card
            assert "Stratification pools container malformed — child inventory unavailable" in bottom_card
            assert 'data-p2-container="pool-float"' not in float_card
            assert "was not emitted" not in float_card
            assert "Elemental pool composition pending" not in float_card
            assert "Elemental pool composition by weight was not emitted" not in float_card

        pool_member = _present_stratification()
        if state == "absent":
            pool_member["pools"].pop("float_layer")
        elif state == "empty":
            pool_member["pools"]["float_layer"] = {}
        elif state == "malformed":
            pool_member["pools"]["float_layer"] = []
        else:
            pool_member["pools"]["float_layer"] = 0
        pool_member_notice = _container_notice(
            _tap_card(_render(_artifact(stratification=pool_member)), "float"),
            "pool-float",
        )
        assert f"<strong>{heading}</strong>" in pool_member_notice
        assert detail in pool_member_notice

        interface = _present_stratification()
        if state == "absent":
            interface.pop("interface")
        elif state == "empty":
            interface["interface"] = {}
        elif state == "malformed":
            interface["interface"] = []
        else:
            interface["interface"] = 0
        interface_html = _render(_artifact(stratification=interface))
        interface_notice = _container_notice(interface_html, "interface")
        assert f"<strong>{heading}</strong>" in interface_notice
        assert detail in interface_notice
        if state in {"malformed", "zero"}:
            assert "Aggressive float-tap assumption: malformed" in _tap_card(
                interface_html,
                "float",
            )

        for field, region in (
            ("buoyancy", "buoyancy"),
            ("density_correlation_provenance", "density-provenance"),
        ):
            nested = _present_stratification()
            if state == "absent":
                nested["pools"]["float_layer"].pop(field)
            elif state == "empty":
                nested["pools"]["float_layer"][field] = {}
            elif state == "malformed":
                nested["pools"]["float_layer"][field] = []
            else:
                nested["pools"]["float_layer"][field] = 0
            nested_card = _tap_card(
                _render(_artifact(stratification=nested)),
                "float",
            )
            nested_notice = _container_notice(nested_card, region)
            assert f"<strong>{heading}</strong>" in nested_notice
            assert detail in nested_notice
            if field == "buoyancy":
                summary_state = {
                    "absent": "not emitted",
                    "empty": "empty",
                    "malformed": "malformed",
                    "zero": "malformed",
                }[state]
                summary_row = _interface_row(nested_card, "Buoyancy verdict")
                assert f"Buoyancy verdict</span><b>{summary_state}</b>" in summary_row

        provenance = _present_stratification()
        if state == "absent":
            provenance.pop("provenance")
        elif state == "empty":
            provenance["provenance"] = {}
        elif state == "malformed":
            provenance["provenance"] = []
        else:
            provenance["provenance"] = 0
        provenance_notice = _container_notice(
            _render(_artifact(stratification=provenance)),
            "provenance",
        )
        assert f"<strong>{heading}</strong>" in provenance_notice
        assert detail in provenance_notice

        final_state_artifact = _artifact(
            stratification=_present_stratification(),
            final_state={"process.metal_phase_bottom_pool": {"Fe": 1.0}},
        )
        if state == "absent":
            final_state_artifact["terminal"].pop("final_state")
        elif state == "empty":
            final_state_artifact["terminal"]["final_state"] = {}
        elif state == "malformed":
            final_state_artifact["terminal"]["final_state"] = []
        else:
            final_state_artifact["terminal"]["final_state"] = 0
        final_state_notice = _container_notice(
            _render(final_state_artifact),
            "final-state",
        )
        assert f"<strong>{heading}</strong>" in final_state_notice
        assert detail in final_state_notice

        terminal_artifact = _artifact(stratification=_present_stratification())
        if state == "absent":
            terminal_artifact.pop("terminal")
        elif state == "empty":
            terminal_artifact["terminal"] = {}
        elif state == "malformed":
            terminal_artifact["terminal"] = []
        else:
            terminal_artifact["terminal"] = 0
        terminal_notice = _container_notice(
            _render(terminal_artifact),
            "terminal",
        )
        expected_terminal_heading = "Empty" if state == "empty" else heading
        assert f"<strong>{expected_terminal_heading}</strong>" in terminal_notice
        assert detail in terminal_notice

    present = _render(_artifact(
        stratification=_present_stratification(),
        final_state={"process.metal_phase_bottom_pool": {"Fe": 1.0}},
    ))
    assert 'data-p2-container="pools"' not in present
    assert 'data-p2-container="pool-float"' not in present
    assert 'data-p2-container="interface"' not in present
    assert 'data-p2-container="buoyancy"' not in present
    assert 'data-p2-container="density-provenance"' not in present
    assert 'data-p2-container="provenance"' not in present
    assert 'data-p2-container="terminal"' not in present
    assert 'data-p2-container="final-state"' not in present


def test_p2_absent_terminal_pool_account_is_not_empty_map() -> None:
    """F11: missing individual final-state pool key is not emitted, not empty."""
    for card_id, present_key, absent_key in (
        ("float", "process.metal_phase_bottom_pool", "process.metal_phase_float_layer"),
        ("bottom", "process.metal_phase_float_layer", "process.metal_phase_bottom_pool"),
    ):
        html = _render(_artifact(
            stratification=_present_stratification(),
            final_state={present_key: {"Fe": 5.0}},
        ))
        terminal = _species_map_region(
            _tap_card(html, card_id),
            "Final-state process pool · mol by species",
        )
        assert "Final-state process-pool mol map not emitted" in terminal
        assert "No species entries present in emitted map" not in terminal

    empty_html = _render(_artifact(
        stratification=_present_stratification(),
        final_state={"process.metal_phase_bottom_pool": {}},
    ))
    empty_terminal = _species_map_region(
        _tap_card(empty_html, "bottom"),
        "Final-state process pool · mol by species",
    )
    assert "No species entries present in emitted map" in empty_terminal
    assert "not emitted" not in empty_terminal

    zero_html = _render(_artifact(
        stratification=_present_stratification(),
        final_state={"process.metal_phase_bottom_pool": {"Fe": 0.0}},
    ))
    zero_terminal = _species_map_region(
        _tap_card(zero_html, "bottom"),
        "Final-state process pool · mol by species",
    )
    assert 'Fe</b> <span title="0 mol">0 mol</span>' in zero_terminal

    malformed_html = _render(_artifact(
        stratification=_present_stratification(),
        final_state={"process.metal_phase_bottom_pool": []},
    ))
    malformed_terminal = _species_map_region(
        _tap_card(malformed_html, "bottom"),
        "Final-state process pool · mol by species",
    )
    assert "Emitted species map is malformed" in malformed_terminal


def test_p2_registers_stable_id_and_uses_shared_species_colors_in_every_region() -> None:
    sentinel = "#123abc"
    html = _render(
        _artifact(stratification=_present_stratification()),
        species_color_sentinel=sentinel,
    )
    upper_pool = _species_map_region(
        _tap_card(html, "float"),
        "Stratified pool · mol by species",
    )
    bottom = _tap_card(html, "bottom")
    bottom_contaminants = _between(
        bottom,
        '<div class="sec-p2-contaminants"',
        '<div class="sec-p2-kv">',
    )
    bottom_provenance = _between(
        bottom,
        "<h3>Density-correlation provenance</h3>",
        '<div class="sec-p2-flags" data-p2-flags="pool">',
    )

    assert 'id="sec-p2-taps"' in html
    assert f'style="--species-color:{sentinel}"' in upper_pool
    assert f'style="--species-color:{sentinel}"' in bottom_contaminants
    assert f'style="--species-color:{sentinel}"' in bottom_provenance


def test_p2_partial_pool_density_stays_not_emitted_without_zero_or_copying_buoyancy() -> None:
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"].pop("density_kg_m3")

    bottom = _tap_card(
        _render(_artifact(stratification=stratification)),
        "bottom",
    )
    row = _interface_row(bottom, "Pool density")

    assert "Pool density</span><b>not emitted</b>" in row
    assert "0 kg/m³" not in row
    assert "7,200 kg/m³" not in row


def test_p2_density_provenance_range_distinguishes_absent_from_malformed() -> None:
    for state, expected in (
        ("absent", "not emitted"),
        ("empty", "malformed"),
        ("short", "malformed"),
        ("malformed_bound", "malformed"),
        ("present", "933–1,190"),
    ):
        stratification = _present_stratification()
        record = stratification["pools"]["float_layer"]["density_correlation_provenance"]["Al"]
        if state == "absent":
            record.pop("valid_range_K")
        elif state == "empty":
            record["valid_range_K"] = []
        elif state == "short":
            record["valid_range_K"] = [933.0]
        elif state == "malformed_bound":
            record["valid_range_K"] = [None, 1190.0]
        else:
            record["valid_range_K"] = [933.0, 1190.0]

        card = _tap_card(_render(_artifact(stratification=stratification)), "float")
        provenance = _between(
            card,
            "<h3>Density-correlation provenance</h3>",
            '<div class="sec-p2-flags" data-p2-flags="pool">',
        )
        if state == "present":
            assert "fitted range " in provenance
            assert "933" in provenance and "1,190" in provenance
            assert "fitted range malformed" not in provenance
            assert "fitted range not emitted" not in provenance
        else:
            assert f"fitted range {expected}" in provenance
            if state != "absent":
                assert "fitted range not emitted" not in provenance
            if state == "malformed_bound":
                # F6 (codex): whole range is malformed — no dash, no residual bound.
                assert "fitted range malformed–" not in provenance
                assert "1,190" not in provenance
                assert "1190" not in provenance
                # Exact token: no numbers glued after malformed.
                assert re.search(r"fitted range malformed(?![–\d])", provenance)


def test_p2_partial_buoyancy_verdict_stays_not_emitted_without_inference() -> None:
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"]["buoyancy"].pop("verdict")
    bottom = _tap_card(
        _render(_artifact(stratification=stratification)),
        "bottom",
    )
    summary = _interface_row(bottom, "Buoyancy verdict")
    detail = _between(
        bottom,
        "<h3>Buoyancy diagnostic</h3>",
        "<h3>Density-correlation provenance</h3>",
    )

    assert "Buoyancy verdict</span><b>not emitted</b>" in summary
    assert "Emitted verdict</span><b>not emitted</b>" in detail
    assert "Float" not in summary and "Sink" not in summary
    assert "Float" not in detail and "Sink" not in detail


def test_p2_partial_buoyancy_numeric_fields_stay_not_emitted_without_derivation() -> None:
    """F9 (codex): each numeric buoyancy field absent despite sibling ingredients."""
    cases = (
        ("delta_rho_kg_m3", "Density contrast", "4,500", "4500"),
        ("alloy_density_kg_m3", "Alloy density", "7,200", "7200"),
        ("melt_density_kg_m3", "Melt density", "2,700", "2700"),
        ("ambiguity_threshold_kg_m3", "Ambiguity threshold", "260", "260"),
        ("melt_density_uncertainty_kg_m3", "Melt-density uncertainty", "135", "135"),
        ("alloy_density_uncertainty_kg_m3", "Alloy-density uncertainty", "230", "230"),
    )
    for field, label, pretty, raw in cases:
        stratification = _present_stratification()
        stratification["pools"]["bottom_pool"]["buoyancy"].pop(field)
        bottom = _tap_card(
            _render(_artifact(stratification=stratification)),
            "bottom",
        )
        detail = _between(
            bottom,
            "<h3>Buoyancy diagnostic</h3>",
            "<h3>Density-correlation provenance</h3>",
        )
        row = _interface_row(detail, label)
        assert f"{label}</span><b>not emitted</b>" in row
        assert pretty not in row
        assert raw not in row
        assert "kg/m³" not in row or "not emitted" in row


def test_p2_prettifies_emitted_buoyancy_ambiguous_verdict_in_summary_and_detail() -> None:
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"]["buoyancy"]["verdict"] = "BUOYANCY-AMBIGUOUS"
    bottom = _tap_card(
        _render(_artifact(stratification=stratification)),
        "bottom",
    )
    summary = _interface_row(bottom, "Buoyancy verdict")
    detail = _between(
        bottom,
        "<h3>Buoyancy diagnostic</h3>",
        "<h3>Density-correlation provenance</h3>",
    )

    assert "Buoyancy verdict</span><b>Buoyancy ambiguous</b>" in summary
    assert "Emitted verdict</span><b>Buoyancy ambiguous</b>" in detail
    assert "BUOYANCY-AMBIGUOUS" not in summary
    assert "BUOYANCY-AMBIGUOUS" not in detail


def test_p2_escapes_untrusted_artifact_values() -> None:
    stratification = _present_stratification()
    hostile_status = '<img src=x onerror="status()">'
    hostile_mode = '<svg onload="mode()">'
    hostile_hour = '<iframe srcdoc="hour()">'
    hostile_species = '<img src=x onerror="boom()">'
    stratification["status"] = hostile_status
    stratification["mode"] = hostile_mode
    stratification["pools"]["bottom_pool"]["composition_wt_pct"] = {
        '<img src=x onerror="boom">': 12.5,
    }
    # F8: provenance species key is a distinct interpolation site.
    stratification["pools"]["bottom_pool"]["density_correlation_provenance"] = {
        hostile_species: {
            "source": '<script>alert("x")</script>',
            "valid_range_K": [1.0, 2.0],
            "temperature_K": 3.0,
            "status": "within_valid_range",
        }
    }

    artifact = _artifact(stratification=stratification)
    artifact["timesteps"][0]["hour"] = hostile_hour
    html = _render(artifact)
    top_flags = _top_flags(html)
    subtitle = _subtitle(html)
    bottom_provenance = _between(
        _tap_card(html, "bottom"),
        "<h3>Density-correlation provenance</h3>",
        '<div class="sec-p2-flags" data-p2-flags="pool">',
    )

    assert '<img src=x onerror="boom">' not in html
    assert '&lt;img src=x onerror=&quot;boom&quot;&gt;' in html
    assert '<script>alert("x")</script>' not in html
    assert '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;' in html
    assert hostile_status not in top_flags
    assert '&lt;img src=x onerror=&quot;status()&quot;&gt;' in top_flags
    assert '&amp;lt;img src=x onerror=&amp;quot;status()&amp;quot;&amp;gt;' not in top_flags
    assert hostile_mode not in top_flags
    assert '&lt;svg onload=&quot;mode()&quot;&gt;' in top_flags
    assert '&amp;lt;svg onload=&amp;quot;mode()&amp;quot;&amp;gt;' not in top_flags
    assert hostile_hour not in subtitle
    assert '&lt;iframe srcdoc=&quot;hour()&quot;&gt;' in subtitle
    assert '&amp;lt;iframe srcdoc=&amp;quot;hour()&amp;quot;&amp;gt;' not in subtitle
    assert hostile_species not in bottom_provenance
    assert '&lt;img src=x onerror=&quot;boom()&quot;&gt;' in bottom_provenance
    assert bottom_provenance.count("&lt;img src=x onerror=&quot;boom()&quot;&gt;") == 1
    assert '&amp;lt;img' not in bottom_provenance


def test_p2_uses_shared_report_labels_esc() -> None:
    """F14: render must call ReportLabels.esc (not a local twin)."""
    html, esc_calls = _render(
        _artifact(stratification=_present_stratification()),
        spy_esc=True,
    )
    assert 'id="sec-p2-taps"' in html
    assert esc_calls > 0


def test_p2_render_tolerates_malformed_roots_without_throwing() -> None:
    """F12 / F8(gk): null/primitive roots still yield a section."""
    for root in (None, 0, "x", [], {"timesteps": None}, {"timesteps": 1}, {"terminal": 1}):
        html = _render(root)  # type: ignore[arg-type]
        assert 'id="sec-p2-taps"' in html
        assert "sec-p2-taps" in html


def test_p2_css_selectors_are_scoped_under_sec_p2_taps() -> None:
    """F13: every concrete selector group is rooted under .sec-p2-taps."""
    css = PANEL_CSS.read_text(encoding="utf-8")
    # Strip comments.
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # Drop @media wrappers' braces content handling: extract selector groups
    # before each '{', skipping @-rules themselves.
    for match in re.finditer(r"([^{}]+)\{", css):
        prelude = match.group(1).strip()
        if not prelude or prelude.startswith("@"):
            continue
        for selector in prelude.split(","):
            selector = selector.strip()
            if not selector:
                continue
            assert selector.startswith(".sec-p2-taps"), (
                f"unscoped selector: {selector!r}"
            )
