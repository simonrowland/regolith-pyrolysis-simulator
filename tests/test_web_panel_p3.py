from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1] / "web" / "report_viewer"


def _render_panel(artifact: dict, *, timestep_index: int | None = None) -> dict:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const hourly = { innerHTML: "" };
const context = {
  document: { querySelector(selector) { return selector === "#sec-p3-wall-coating-hourly" ? hourly : null; } },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const panel = context.ReportPanels[0];
const html = panel.render(artifact, artifact.timesteps.map((step) => step.summary), [], {});
const index = process.argv[5] === "none" ? null : Number(process.argv[5]);
if (index !== null) panel.onTimestep(artifact, index);
process.stdout.write(JSON.stringify({ html, hourly: hourly.innerHTML, id: panel.id }));
"""
    completed = subprocess.run(
        [
            "node", "-", str(ROOT / "labels.js"),
            str(ROOT / "panels" / "p3-wall-coating.js"),
            json.dumps(artifact),
            "none" if timestep_index is None else str(timestep_index),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _artifact() -> dict:
    return {"terminal": {}, "timesteps": []}


def test_p3_renders_species_segments_flux_kn_and_selected_hour() -> None:
    artifact = _artifact()
    artifact["terminal"] = {
        "final": {
            "wall_deposit_by_species_kg": {"Fe": 0.01, "SiO": 0.25, "Na": 0.0},
            "deposit_by_surface_species_kg": {
                "stage_0_to_stage_1": {"SiO": 0.2},
                "stage_1_to_stage_2": {"Fe": 0.01},
            },
            "pump_outlet_by_species_kg": "not_applicable_until_p0",
        },
        "run_metadata": {
            "pressure_coating_pareto_diagnostic": {
                "status": "ok",
                "current": {
                    "wall_deposit_flux_kg_hr_by_species": {"SiO": 0.003},
                    "wall_deposit_cumulative_kg_by_species": {"SiO": 0.24},
                },
                "by_species": {
                    "SiO": {
                        "status": "ok",
                        "current_wall_deposit_flux_kg_hr": 0.004,
                        "cumulative_wall_deposit_kg": 0.25,
                    }
                },
            },
            "knudsen_regime_diagnostic": {
                "status": "ok", "regime": "viscous", "knudsen_number": 0.0038,
                "carrier_gas": "O2",
            },
        },
    }
    artifact["timesteps"] = [
        {
            "hour": 7,
            "summary": {
                "wall_deposit_delta_kg": {"stage_0_to_stage_1": {"SiO": 0.004}},
                "wall_deposit_cumulative_kg": {"stage_0_to_stage_1": {"SiO": 0.25}},
            },
        }
    ]

    rendered = _render_panel(artifact, timestep_index=0)
    html = rendered["html"]

    assert rendered["id"] == "sec-p3-wall-coating"
    assert html.index('title="SiO"') < html.index('title="Fe"')
    assert "0.25 kg" in html and "0.004 kg/hr" in html
    assert "Wall deposit · stage 0→1" in html
    assert "Regime</b> viscous" in html and "Kn</b> 0.0038" in html
    assert "SiO watch species" in html
    assert "Pump outlet</b> not applicable until p0" in html
    assert "0 kg" in html and "trace" not in html
    assert "Selected hour · 7" in rendered["hourly"]
    assert "0.004 kg/hour" in rendered["hourly"] and "0.25 kg" in rendered["hourly"]

    diagnostic_only = _artifact()
    diagnostic_only["terminal"] = {
        "run_metadata": {
            "pressure_coating_pareto_diagnostic": {
                "current": {
                    "wall_deposit_cumulative_kg_by_species": {"Fe": 0.01, "SiO": 0.25},
                },
            }
        }
    }
    diagnostic_html = _render_panel(diagnostic_only)["html"]

    assert diagnostic_html.index('title="SiO"') < diagnostic_html.index('title="Fe"')
    assert "Terminal aggregate pending" in diagnostic_html


def test_p3_absent_fields_stay_pending_without_coating_verdict() -> None:
    html = _render_panel(_artifact())["html"]

    assert "Per-species wall deposits pending" in html
    assert "Per-segment deposits pending" in html
    assert "Transport context pending" in html
    assert "Wall lifetime not assessed" in html
    assert "no CLEAR or ruined verdict is issued" in html
    assert "0 kg" not in html

    for malformed_lifetime in (None, "unknown", [], {}):
        artifact = _artifact()
        artifact["terminal"]["wall_lifetime"] = malformed_lifetime
        malformed_html = _render_panel(artifact)["html"]
        assert "Wall lifetime not assessed" in malformed_html
        assert "Wall lifetime diagnostic emitted" not in malformed_html

    sparse_knudsen = _artifact()
    sparse_knudsen["terminal"] = {
        "run_metadata": {"knudsen_regime_diagnostic": {}},
    }
    sparse_html = _render_panel(sparse_knudsen)["html"]
    assert "Transport context pending" in sparse_html


def test_p3_surfaces_diagnostic_authority_uncertainty_and_escapes_values() -> None:
    artifact = _artifact()
    artifact["terminal"] = {
        "final": {"wall_deposit_by_species_kg": {"SiO<script>": 0.5}},
        "run_metadata": {
            "pressure_coating_pareto_diagnostic": {
                "status": "provisional",
                "authoritative": False,
                "diagnostic_only": True,
                "high_uncertainty": True,
                "source": "model <draft>",
                "by_species": {
                    "SiO<script>": {
                        "status": "unavailable",
                        "reason": "coverage <missing>",
                    }
                },
            },
            "knudsen_regime_diagnostic": {
                "status": "provisional",
                "stage_area_geometry_provenance_notice": {
                    "status": "provisional",
                    "provisional": True,
                    "output_status": "status_bearing",
                    "source_class": "engineering-default",
                    "message": 'geometry "not certified"',
                },
            },
        },
    }

    html = _render_panel(artifact)["html"]

    assert "Authoritative</b> no" in html
    assert "Diagnostic only</b> yes" in html
    assert "High uncertainty</b> yes" in html
    assert "Source</b> model &lt;draft&gt;" in html
    assert "Reason</b> coverage &lt;missing&gt;" in html
    assert "Source class</b> engineering default" in html
    assert "Provisional</b> yes" in html
    assert "Output status</b> status bearing" in html
    assert "SiO&lt;script&gt;" in html
    assert "<script>" not in html and 'geometry "not certified"' not in html


def test_p3_partial_maps_never_backfill_terminal_or_flux_values() -> None:
    artifact = _artifact()
    artifact["terminal"] = {
        "final": {
            "deposit_by_surface_species_kg": {
                "stage_0_to_stage_1": {"SiO": 1.0},
                "stage_1_to_stage_2": {"SiO": 0.271},
            }
        },
        "run_metadata": {
            "pressure_coating_pareto_diagnostic": {
                "current": {"wall_deposit_cumulative_kg_by_species": {"SiO": 1.11}},
                "by_species": {"SiO": {"status": "ok"}},
            }
        },
    }
    artifact["timesteps"] = [
        {
            "hour": 3,
            "summary": {
                "wall_deposit_delta_kg": {"stage_0_to_stage_1": {"SiO": 0.271}},
                "wall_deposit_cumulative_kg": {"stage_0_to_stage_1": {"SiO": 1.0}},
            },
        }
    ]

    html = _render_panel(artifact)["html"]
    species_row = html[html.index('title="SiO"'):html.index("</tr>", html.index('title="SiO"'))]

    assert "Terminal aggregate pending" in html
    assert "Terminal aggregate · kg" in html
    assert species_row.count("not emitted") >= 2
    assert "1.11 kg" in species_row
    assert "1.271 kg" not in species_row
    assert "0.271 kg/hour" not in species_row
    assert "Wall lifetime not assessed" in html
    assert "<b>CLEAR" not in html and "<b>ruined" not in html

    reverse = _artifact()
    reverse["terminal"] = {
        "final": {"wall_deposit_by_species_kg": {"SiO": 2.345}},
        "run_metadata": {
            "pressure_coating_pareto_diagnostic": {
                "by_species": {"SiO": {"status": "ok"}},
            }
        },
    }
    reverse["timesteps"] = [
        {
            "hour": 4,
            "summary": {
                "wall_deposit_delta_kg": {"stage_0_to_stage_1": {"SiO": 0.333}},
            },
        },
        {
            "hour": 5,
            "summary": {
                "wall_deposit_cumulative_kg": {"stage_0_to_stage_1": {"SiO": 0.8}},
            },
        },
    ]

    reverse_render = _render_panel(reverse, timestep_index=0)
    reverse_html = reverse_render["html"]
    reverse_row = reverse_html[
        reverse_html.index('title="SiO"'):
        reverse_html.index("</tr>", reverse_html.index('title="SiO"'))
    ]

    assert reverse_row.count("2.345 kg") == 1
    assert reverse_row.count("not emitted") >= 2
    assert "Per-segment deposits pending" in reverse_html
    assert "0.333 kg/hour" in reverse_render["hourly"]
    assert "0.333 kg</span>" not in reverse_render["hourly"]

    reverse_last = _render_panel(reverse, timestep_index=1)["hourly"]
    assert "0.8 kg" in reverse_last
    assert "0.8 kg/hour" not in reverse_last
