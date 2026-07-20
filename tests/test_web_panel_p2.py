from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p2-taps.js"


def _render(artifact: dict) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const rows = (artifact.timesteps || []).map((timestep) => timestep.summary || {});
process.stdout.write(context.ReportPanels[0].render(artifact, rows, [], {}));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


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
        "interface": {
            "area_m2": 0.2,
            "float_layer_mass_kg": 0.25,
            "equivalent_film_thickness_m": 0.0005,
            "coverage_reference_thickness_m": 0.001,
            "coverage_fraction_at_reference_thickness": 0.5,
            "aggressive_float_tap_assumption_load_bearing": True,
        },
    }


def test_p2_renders_two_taps_contaminants_accounts_and_geometry() -> None:
    html = _render(_artifact(
        stratification=_present_stratification(),
        final_state={
            "process.metal_phase_float_layer": {"Al": 7.0, "Si": 1.0},
            "process.metal_phase_bottom_pool": {"Fe": 100.0, "Co": 2.0, "Ni": 1.0},
        },
    ))

    assert 'id="sec-p2-taps"' in html
    assert "Upper float tap" in html
    assert "Bottom pool tap" in html
    assert "Fe 97.1 wt% · largest emitted share" in html
    assert 'Co contaminant</b> <span title="1.9 wt%">1.9 wt%</span>' in html
    assert 'Ni contaminant</b> <span title="1 wt%">1 wt%</span>' in html
    assert "Terminal ledger account · mol by species" in html
    assert 'Fe</b> <span title="100 mol">100 mol</span>' in html
    assert "Equivalent film thickness" in html and "5.00e-4 m" in html
    assert "Coverage fraction at reference thickness" in html and ">0.5<" in html
    assert "Frozen kg tap views pending producer attach" in html


def test_p2_absent_stratification_stays_pending_without_hiding_terminal_mol() -> None:
    html = _render(_artifact(
        stratification=None,
        final_state={"process.metal_phase_bottom_pool": {"Fe": 3.0}},
    ))

    assert "Pending metal-phase stratification" in html
    assert "Elemental tap grade pending" in html
    assert "Pool mol amounts are not converted into a grade" in html
    assert 'Fe</b> <span title="3 mol">3 mol</span>' in html
    assert "0 wt%" not in html


def test_p2_partial_composition_does_not_derive_wt_pct_from_species_mol() -> None:
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"].pop("composition_wt_pct")
    stratification["pools"]["bottom_pool"]["species_mol"] = {"Fe": 2.0, "Co": 1.0}

    html = _render(_artifact(stratification=stratification))

    assert "Elemental composition by weight was not emitted" in html
    assert "Pool mol amounts are not converted into a grade" in html
    assert "66.67 wt%" not in html
    assert 'Fe</b> <span title="2 mol">2 mol</span>' in html
    assert 'Co</b> <span title="1 mol">1 mol</span>' in html


def test_p2_partial_interface_does_not_derive_geometry() -> None:
    missing_mass = _present_stratification()
    missing_mass["interface"].pop("float_layer_mass_kg")
    mass_html = _render(_artifact(stratification=missing_mass))

    missing_thickness = _present_stratification()
    missing_thickness["interface"].pop("equivalent_film_thickness_m")
    thickness_html = _render(_artifact(stratification=missing_thickness))

    missing_coverage = _present_stratification()
    missing_coverage["interface"].pop("coverage_fraction_at_reference_thickness")
    coverage_html = _render(_artifact(stratification=missing_coverage))

    assert "Float-layer mass</span><b>not emitted</b>" in mass_html
    assert "Equivalent film thickness</span><b>not emitted</b>" in thickness_html
    assert "5.58e-4 m" not in thickness_html
    marker = "Coverage fraction at reference thickness</span><b>not emitted</b>"
    assert marker in coverage_html
    assert "Coverage fraction at reference thickness</span><b><span" not in coverage_html


def test_p2_surfaces_diagnostic_authority_and_load_bearing_flags() -> None:
    html = _render(_artifact(stratification=_present_stratification()))

    assert "Diagnostic only · no tap gate" in html
    assert "Extraction behavior unchanged · diagnostic only" in html
    assert "Fallback basaltic-melt constant · engine density unavailable" in html
    assert "Melt-density fallback engaged:</b> true" in html
    assert "Aggressive float-tap assumption: load-bearing" in html
    assert "Aggressive float-tap assumption</span><b>load-bearing" in html
    assert "Extrapolated below valid range" in html
    assert "Assael iron source" in html


def test_p2_escapes_untrusted_artifact_values() -> None:
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"]["composition_wt_pct"] = {
        '<img src=x onerror="boom">': 12.5,
    }
    stratification["pools"]["bottom_pool"]["density_correlation_provenance"] = {
        "Fe": {
            "source": '<script>alert("x")</script>',
            "valid_range_K": [1.0, 2.0],
            "temperature_K": 3.0,
            "status": "within_valid_range",
        }
    }

    html = _render(_artifact(stratification=stratification))

    assert '<img src=x onerror="boom">' not in html
    assert '&lt;img src=x onerror=&quot;boom&quot;&gt;' in html
    assert '<script>alert("x")</script>' not in html
    assert '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;' in html
