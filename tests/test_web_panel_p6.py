from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p6-mre.js"


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
const rows = Array.isArray(artifact.timesteps)
  ? artifact.timesteps.map((step) => step && step.summary)
  : [];
const panel = context.ReportPanels.find((entry) => entry.id === "sec-p6-mre");
process.stdout.write(panel.render(artifact, rows, [], {}));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


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

    assert 'id="sec-p6-mre"' in html
    assert "diagnostic_uncertified" in html
    assert "authoritative_ellingham_graph_with_static_fallback" in html
    assert "SiO₂" in html
    assert "0.39 V" in html
    assert "1.006 V" in html
    assert "1.25 kg" in html
    assert "17.4 mol" in html
    assert "42 mol" in html
    assert html.count("Pending producer") == 3


def test_p6_absent_subtrees_have_separate_pending_states_without_zeroes() -> None:
    html = _render(_artifact({}))

    assert "Pending ladder evidence" in html
    assert "Pending uncertified yield" in html
    assert "Pending MRE-anode O₂" in html
    assert html.count("Pending producer") == 3
    for fabricated in ("0 V", "0 A", "0 kg/h", "0 mol"):
        assert fabricated not in html


def test_p6_surfaces_nested_authority_uncertainty_and_escapes_values() -> None:
    ladder = _ladder()
    ladder["non_authoritative_voltage_by_oxide"]["MnO"]["status"] = (
        "<script>authority breach</script>"
    )
    summary = {
        "mre_ellingham_ladder_diagnostic": ladder,
        "mre_uncertified_yield": {
            "FeO": {
                "source_species": "Fe2O3",
                "produced_species": "FeO",
                "produced_kg": 0.2,
                "produced_mol": 2.8,
                "certification": "uncertified",
                "reference_V": 0.65,
                "reference_status": "provisional",
                "reason": "diagnostic evidence",
                "authoritative": False,
                "diagnostic_only": True,
                "high_uncertainty": True,
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

    assert "Voltage Authoritative" in html
    assert (
        '<b>Authoritative</b>false</span><span class="sec-p6-chip sec-p6-warn">'
        '<b>Status</b>&lt;script&gt;authority breach&lt;/script&gt;'
        in html
    )
    assert "Diagnostic only" in html and "true" in html
    assert "High uncertainty" in html
    assert "per_tick" in html
    assert "builtin" in html
    assert "heuristic" in html
    assert "Skip reason" in html
    assert "&lt;script&gt;authority breach&lt;/script&gt;" in html
    assert "<script>authority breach</script>" not in html


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

    assert "Delta vs declared · V" in html
    assert "0.4 V" not in html
    assert "1273 K" not in html and "1,273 K" not in html
    assert (
        '<span>Produced amount · kg</span><b><span class="sec-p6-not-emitted">'
        "not emitted</span></b>"
        in html
    )
    assert "0.07184 kg" not in html
    assert "Pending reordering evidence" in html
    assert "Pending non-authoritative voltage entries" in html
    assert "Pending MRE-anode O₂" in html
    assert "99 mol" not in html
    assert html.count("Pending producer") == 3
