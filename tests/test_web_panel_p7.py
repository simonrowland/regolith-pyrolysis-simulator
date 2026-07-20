import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
LABELS_PATH = ROOT / "labels.js"
PANEL_PATH = ROOT / "panels" / "p7-energy.js"


def _artifact(*summaries: dict) -> dict:
    return {
        "timesteps": [{"summary": summary} for summary in summaries],
    }


def _render_panel(artifact: dict) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const rows = artifact.timesteps.map((timestep) => timestep.summary);
const panel = context.ReportPanels.find((candidate) => candidate.id === "sec-p7-energy");
process.stdout.write(panel.render(artifact, rows, [], {}));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS_PATH), str(PANEL_PATH), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _complete_summary(**overrides) -> dict:
    summary = {
        "energy_electrical_kWh": 2.75,
        "energy_evaporation_thermal_kWh": 7.25,
        "energy_latent_kWh": 1.25,
        "energy_dissociation_kWh": 6.0,
        "energy_electrical_plus_evaporation_kWh": 10.0,
        "energy_electrical_plus_evaporation_cumulative_kWh": 123.5,
        "energy_cumulative_breakdown_kWh": {
            "electrical": 80.25,
            "evaporation_thermal": 43.25,
            "latent": 12.5,
            "dissociation": 30.75,
            "electrical_plus_evaporation": 123.5,
            "custom_heat_sink": 4.125,
        },
        "energy_evaporation_breakdown_kWh": {
            "evaporation_enthalpy_sink": 7.25,
            "reaction_disproportionation_enthalpy_sink": 6.0,
            "product_vapor_enthalpy_sink": 1.25,
            "net_unallocated": 0.0,
            "custom_trace": 0.125,
        },
        "energy_scope": "electrical_plus_known_evaporation_enthalpy",
        "furnace_heat_status": "partial",
    }
    summary.update(overrides)
    return summary


def test_present_fields_render_emitted_values_and_readable_breakdown_keys():
    html = _render_panel(_artifact(_complete_summary()))

    assert 'id="sec-p7-energy"' in html
    assert (
        '<h3>Cumulative electrical load</h3>'
        '<div class="sec-p7-energy-value">80.25 kWh</div>'
        '<p class="sec-p7-energy-basis">Cumulative through the terminal timestep</p>'
    ) in html
    assert (
        '<h3>Cumulative known evaporation thermal sink</h3>'
        '<div class="sec-p7-energy-value">43.25 kWh</div>'
        '<p class="sec-p7-energy-basis">Cumulative through the terminal timestep</p>'
    ) in html
    assert (
        '<h3>Electrical energy</h3>'
        '<div class="sec-p7-energy-value">2.75 kWh</div>'
        '<p class="sec-p7-energy-basis">Terminal timestep · one-hour interval</p>'
    ) in html
    assert (
        '<h3>Known evaporation thermal sink</h3>'
        '<div class="sec-p7-energy-value">7.25 kWh</div>'
        '<p class="sec-p7-energy-basis">Terminal timestep · one-hour interval</p>'
    ) in html
    assert (
        '<h3>Latent vaporization component</h3>'
        '<div class="sec-p7-energy-value">1.25 kWh</div>'
        '<p class="sec-p7-energy-basis">Terminal timestep · one-hour interval</p>'
    ) in html
    assert (
        '<h3>Dissociation component</h3>'
        '<div class="sec-p7-energy-value">6 kWh</div>'
        '<p class="sec-p7-energy-basis">Terminal timestep · one-hour interval</p>'
    ) in html
    assert (
        '<h3>Terminal-timestep scoped combined energy</h3>'
        '<div class="sec-p7-energy-value">10 kWh</div>'
        '<p class="sec-p7-energy-basis">Terminal timestep · one-hour interval · not viewer-summed</p>'
    ) in html
    assert (
        '<h3>Cumulative scoped combined energy</h3>'
        '<div class="sec-p7-energy-value">123.5 kWh</div>'
        '<p class="sec-p7-energy-basis">Cumulative through the terminal timestep · not viewer-summed</p>'
    ) in html
    assert '<th scope="row">Electrical load</th><td>80.25 kWh</td>' in html
    assert '<th scope="row">Evaporation enthalpy sink</th><td>7.25 kWh</td>' in html
    assert "Custom heat sink" in html
    assert "Custom trace" in html
    assert "custom_heat_sink" not in html
    assert "not viewer-summed" in html


def test_absent_fields_render_pending_without_zero_fallback():
    html = _render_panel(_artifact({}))

    assert "Cumulative electrical load" in html
    assert "Known evaporation thermal sink" in html
    assert "pending · not emitted" in html
    assert "Breakdown not emitted; no components are inferred." in html
    assert ">0 kWh<" not in html
    assert "Scope</b> · Not emitted" in html
    assert "Furnace heat</b> · Not emitted" in html


def test_partial_inputs_do_not_manufacture_derived_energy():
    hourly_html = _render_panel(_artifact(
        {
            "energy_electrical_kWh": 13.0,
            "energy_evaporation_thermal_kWh": 4.0,
        },
        {
            "energy_electrical_kWh": 17.0,
            "energy_evaporation_thermal_kWh": 7.0,
            "energy_cumulative_breakdown_kWh": {
                "latent": 2.0,
                "dissociation": 5.0,
            },
        },
    ))
    thermal_html = _render_panel(_artifact({
        "energy_latent_kWh": 3.0,
        "energy_dissociation_kWh": 8.0,
    }))
    cumulative_html = _render_panel(_artifact({
        "energy_cumulative_breakdown_kWh": {
            "electrical": 70.0,
            "evaporation_thermal": 20.0,
        },
    }))

    assert "24 kWh" not in hourly_html
    assert "30 kWh" not in hourly_html
    assert "11 kWh" not in hourly_html
    assert "41 kWh" not in hourly_html
    assert "11 kWh" not in thermal_html
    assert "90 kWh" not in cumulative_html
    assert "pending · not emitted" in hourly_html
    assert "pending · not emitted" in thermal_html
    assert "pending · not emitted" in cumulative_html


def test_sparse_breakdowns_keep_missing_components_pending():
    html = _render_panel(_artifact({
        "energy_cumulative_breakdown_kWh": {"electrical": 50.0},
        "energy_evaporation_breakdown_kWh": {
            "evaporation_enthalpy_sink": 5.0,
        },
        "energy_scope": "electrical_plus_known_evaporation_enthalpy",
        "furnace_heat_status": "partial",
    }))

    assert (
        '<th scope="row">Known evaporation thermal sink</th>'
        '<td><span class="sec-p7-energy-missing">pending · not emitted</span></td>'
    ) in html
    assert (
        '<th scope="row">Net unallocated</th>'
        '<td><span class="sec-p7-energy-missing">pending · not emitted</span></td>'
    ) in html
    assert ">0 kWh<" not in html


def test_electrical_only_scope_and_heat_status_repeat_on_every_card():
    html = _render_panel(_artifact(_complete_summary(
        energy_scope="electrical_only",
        furnace_heat_status="not_tracked",
    )))

    assert html.count("Electrical only") == 10
    assert html.count("Not tracked") == 10
    assert html.count('aria-label="Energy scope and furnace heat coverage"') == 10


def test_artifact_labels_are_escaped():
    html = _render_panel(_artifact(_complete_summary(
        energy_scope='<img src=x onerror="alert(1)">',
        furnace_heat_status='" onmouseover="alert(2)',
        energy_cumulative_breakdown_kWh={
            "electrical": 1.0,
            "<script>alert(3)</script>": 2.0,
        },
    )))

    assert '<img src=x onerror="alert(1)">' not in html
    assert '<script>alert(3)</script>' not in html
    assert '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;' in html
    assert '&quot; onmouseover=&quot;alert(2)' in html
    assert '&lt;script&gt;alert(3)&lt;/script&gt;' in html
