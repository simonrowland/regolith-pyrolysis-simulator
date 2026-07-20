from __future__ import annotations

from html import unescape
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1] / "web" / "report_viewer"


def _render_panel(
    artifact: object,
    *,
    timestep_index: int | None = None,
    target_present: bool = True,
) -> dict:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const hourly = { innerHTML: "" };
const targetPresent = process.argv[6] === "yes";
const context = {
  document: { querySelector(selector) { return targetPresent && selector === "#sec-p3-wall-coating-hourly" ? hourly : null; } },
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const panel = context.ReportPanels[0];
const html = panel.render(artifact);
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
            "yes" if target_present else "no",
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _artifact() -> dict:
    return {"terminal": {}, "timesteps": []}


def _region(rendered: str, start_marker: str, end_marker: str) -> str:
    start = rendered.index(start_marker)
    return rendered[start:rendered.index(end_marker, start)]


def _row_with(rendered: str, marker: str) -> str:
    marker_index = rendered.index(marker)
    start = rendered.rindex("<tr", 0, marker_index)
    return rendered[start:rendered.index("</tr>", marker_index) + len("</tr>")]


def _cell_texts(row: str) -> list[str]:
    cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row)
    return [
        " ".join(unescape(re.sub(r"<[^>]+>", " ", cell)).split())
        for cell in cells
    ]


def _species_text_and_title(row: str) -> tuple[str, str]:
    match = re.search(
        r'<span class="sec-p3-species" title="([^"]*)"><i [^>]*></i>(.*?)</span>',
        row,
    )
    assert match is not None
    return unescape(match.group(2)), unescape(match.group(1))


def test_p3_renders_species_segments_flux_kn_and_selected_hour() -> None:
    artifact = _artifact()
    artifact["terminal"] = {
        "final": {
            "wall_deposit_by_species_kg": {"Fe": 0.01, "SiO": 0.25, "Na": 0.0},
            "deposit_by_surface_species_kg": {
                "stage_0_to_stage_1": {"SiO": 0.2, "Na": 0.03},
                "stage_1_to_stage_2": {"Fe": 0.01},
            },
            "pump_outlet_by_species_kg": "not_applicable_until_p0",
        },
        "run_metadata": {
            "pressure_coating_pareto_diagnostic": {
                "status": "ok",
                "current": {
                    "wall_deposit_flux_kg_hr_by_species": {"SiO": 0.003},
                    "wall_deposit_cumulative_kg_by_species": {"SiO": 0.23},
                },
                "by_species": {
                    "SiO": {
                        "status": "ok",
                        "current_wall_deposit_flux_kg_hr": 0.004,
                        "cumulative_wall_deposit_kg": 0.24,
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
                "wall_deposit_delta_kg": {
                    "stage_0_to_stage_1": {"SiO": 0.004, "Na": 0.001},
                    "stage_1_to_stage_2": {"Fe": 0.002},
                },
                "wall_deposit_cumulative_kg": {
                    "stage_0_to_stage_1": {"SiO": 0.25, "Na": 0.03},
                    "stage_1_to_stage_2": {"Fe": 0.01},
                },
            },
        }
    ]

    rendered = _render_panel(artifact, timestep_index=0)
    html = rendered["html"]
    species_table = _region(
        html,
        "<h3>Wall deposit by species</h3>",
        '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
    )
    sio_row = _row_with(species_table, 'title="SiO"')
    fe_row = _row_with(species_table, 'title="Fe"')
    na_row = _row_with(species_table, 'title="Na"')
    segment_region = _region(
        html,
        "<summary>Per-wall-segment terminal breakdown</summary>",
        "</details>",
    )

    assert rendered["id"] == "sec-p3-wall-coating"
    assert species_table.index('title="SiO"') < species_table.index('title="Fe"')
    assert _cell_texts(sio_row) == [
        "SiO SiO watch species", "0.25 kg", "0.004 kg/hr", "0.24 kg", "Status ok",
    ]
    assert _cell_texts(fe_row)[1:] == [
        "0.01 kg", "not emitted", "not emitted", "Per-species status not emitted",
    ]
    assert _cell_texts(na_row)[1] == "0 kg"
    assert 'style="background:#7570b3"' in sio_row
    assert 'style="background:#d95f02"' in fe_row
    assert _cell_texts(_row_with(segment_region, 'title="SiO"')) == [
        "Wall deposit · stage 0→1", "SiO", "0.2 kg",
    ]
    assert _cell_texts(_row_with(segment_region, 'title="Na"')) == [
        "Wall deposit · stage 0→1", "Na", "0.03 kg",
    ]
    assert _cell_texts(_row_with(segment_region, 'title="Fe"')) == [
        "Wall deposit · stage 1→2", "Fe", "0.01 kg",
    ]
    assert "Regime</b> viscous" in html and "Kn</b> 0.0038" in html
    assert "SiO watch species" in html
    assert "Pump outlet</b> not applicable until p0" in html
    assert "Selected hour · 7" in rendered["hourly"]
    assert _cell_texts(_row_with(rendered["hourly"], 'title="SiO"')) == [
        "Wall deposit · stage 0→1", "SiO", "0.004 kg/hour", "0.25 kg",
    ]
    assert _cell_texts(_row_with(rendered["hourly"], 'title="Na"')) == [
        "Wall deposit · stage 0→1", "Na", "0.001 kg/hour", "0.03 kg",
    ]
    assert _cell_texts(_row_with(rendered["hourly"], 'title="Fe"')) == [
        "Wall deposit · stage 1→2", "Fe", "0.002 kg/hour", "0.01 kg",
    ]
    assert "Terminal aggregate · kg" in species_table
    assert "Current coating flux · kg/hr" in species_table
    assert "Diagnostic cumulative · kg" in species_table
    assert "mol" not in species_table

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

    pump_map = _artifact()
    pump_map["terminal"] = {
        "final": {"pump_outlet_by_species_kg": {"Fe": 0.02, "SiO": 0.0}},
    }
    pump_html = _render_panel(pump_map)["html"]
    pump_region = _region(
        pump_html,
        "<summary>Pump-outlet loss context</summary>",
        "</details>",
    )
    assert _cell_texts(_row_with(pump_region, 'title="Fe"')) == ["Fe", "0.02 kg"]
    assert _cell_texts(_row_with(pump_region, 'title="SiO"')) == ["SiO", "0 kg"]


def test_p3_absent_fields_stay_pending_without_coating_verdict() -> None:
    html = _render_panel(_artifact())["html"]

    assert "Per-species wall deposits pending" in html
    assert "Per-segment deposits pending" in html
    assert "Transport context pending" in html
    assert "Wall lifetime not assessed" in html
    lifetime_region = _region(html, '<div class="sec-p3-lifetime">', "</div>")
    assert "no viewer pass/fail verdict is issued" in lifetime_region
    assert all(token not in lifetime_region for token in ("CLEAR", "ruined", "campaigns_to_resinter"))
    assert "0 kg" not in html

    for malformed_lifetime in (None, "unknown", []):
        artifact = _artifact()
        artifact["terminal"]["wall_lifetime"] = malformed_lifetime
        malformed_html = _render_panel(artifact)["html"]
        assert "Wall lifetime malformed" in malformed_html
        assert "Wall lifetime diagnostic emitted" not in malformed_html

    empty_lifetime = _artifact()
    empty_lifetime["terminal"]["wall_lifetime"] = {}
    empty_lifetime_html = _render_panel(empty_lifetime)["html"]
    assert "Wall lifetime empty" in empty_lifetime_html
    assert "Wall lifetime malformed" not in empty_lifetime_html

    sparse_knudsen = _artifact()
    sparse_knudsen["terminal"] = {
        "run_metadata": {"knudsen_regime_diagnostic": {}},
    }
    sparse_html = _render_panel(sparse_knudsen)["html"]
    assert "Transport context pending" in sparse_html

    empty_knudsen = _artifact()
    empty_knudsen["terminal"] = {
        "run_metadata": {
            "knudsen_regime_diagnostic": {
                "status": "ok", "reason": "", "regime": "", "carrier_gas": "  ",
                "knudsen_number": 0,
                "stage_area_geometry_provenance_notice": {"message": ""},
            }
        }
    }
    empty_transport = _region(
        _render_panel(empty_knudsen)["html"],
        "<span>Transport context</span>",
        '<div class="sec-p3-coating-status">',
    )
    assert "Regime</b> empty" in empty_transport
    assert "Carrier</b> empty" in empty_transport
    assert "Reason</b> empty" in empty_transport
    assert "Kn</b> 0" in empty_transport
    assert "Details</b> empty" in empty_transport

    malformed_knudsen = _artifact()
    malformed_knudsen["terminal"] = {
        "run_metadata": {
            "knudsen_regime_diagnostic": {
                "regime": {}, "carrier_gas": [], "knudsen_number": "zero",
            }
        }
    }
    malformed_transport = _region(
        _render_panel(malformed_knudsen)["html"],
        "<span>Transport context</span>",
        '<div class="sec-p3-coating-status">',
    )
    assert "Regime</b> malformed (object)" in malformed_transport
    assert "Carrier</b> malformed (array)" in malformed_transport
    assert "Kn</b> malformed (string)" in malformed_transport

    for final_value, expected in (
        ({"wall_deposit_by_species_kg": {}}, "Terminal aggregate empty"),
        ({"wall_deposit_by_species_kg": []}, "Terminal aggregate malformed"),
        ({"deposit_by_surface_species_kg": {}}, "Per-segment map empty"),
        ({"deposit_by_surface_species_kg": []}, "Per-segment deposits malformed"),
        ({"pump_outlet_by_species_kg": ""}, "Pump-outlet context empty"),
        ({"pump_outlet_by_species_kg": []}, "Pump-outlet context malformed"),
        ({"pump_outlet_by_species_kg": {}}, "Pump-outlet map empty"),
    ):
        state_artifact = _artifact()
        state_artifact["terminal"]["final"] = final_value
        assert expected in _render_panel(state_artifact)["html"]


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
    coating_region = _region(
        html,
        '<div class="sec-p3-coating-status">',
        "<h3>Wall deposit by species</h3>",
    )
    species_table = _region(
        html,
        "<h3>Wall deposit by species</h3>",
        '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
    )
    species_row = _row_with(species_table, 'title="SiO&lt;script&gt;"')
    geometry_region = _region(html, '<div class="sec-p3-geometry">', "</div></div>")

    assert "Coating replay diagnostic status" in coating_region
    assert "Diagnostic-only surface (schema)" in coating_region
    assert "Authoritative</b> no" in coating_region
    assert "Diagnostic only</b> yes" in coating_region
    assert "High uncertainty</b> yes" in coating_region
    assert "Source</b> model &lt;draft&gt;" in coating_region
    assert "Reason</b> coverage &lt;missing&gt;" in species_row
    assert "Source class</b> engineering default" in geometry_region
    assert "Provisional</b> yes" in geometry_region
    assert "Output status</b> status bearing" in geometry_region
    assert _species_text_and_title(species_row) == ("SiO<script>", "SiO<script>")
    assert 'title="geometry &quot;not certified&quot;"' in geometry_region
    assert "<script>" not in species_row and 'geometry "not certified"' not in geometry_region


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
    species_row = _row_with(html, 'title="SiO"')
    species_cells = _cell_texts(species_row)

    assert "Terminal aggregate pending" in html
    assert "Terminal aggregate · kg" in html
    assert species_cells[1:] == [
        "not emitted", "not emitted", "1.11 kg", "Status ok",
    ]
    assert "1.271 kg" not in species_row
    assert "0.271 kg/hour" not in species_row
    assert "Wall lifetime not assessed" in html
    lifetime_region = _region(html, '<div class="sec-p3-lifetime">', "</div>")
    assert all(token not in lifetime_region for token in ("CLEAR", "ruined", "campaigns_to_resinter"))

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
    reverse_row = _row_with(reverse_html, 'title="SiO"')

    assert _cell_texts(reverse_row)[1:] == [
        "2.345 kg", "not emitted", "not emitted", "Status ok",
    ]
    assert "Per-segment deposits pending" in reverse_html
    assert "0.333 kg/hour" not in reverse_row
    assert "0.8 kg" not in reverse_row
    assert "0.333 kg/hour" in reverse_render["hourly"]
    assert "0.333 kg</span>" not in reverse_render["hourly"]

    reverse_last = _render_panel(reverse, timestep_index=1)["hourly"]
    assert "0.8 kg" in reverse_last
    assert "0.8 kg/hour" not in reverse_last


def test_p3_production_shape_labels_schema_and_fallback_status_precisely() -> None:
    sample = json.loads((ROOT / "sample-run-artifact.json").read_text())
    coating = sample["terminal"]["run_metadata"]["pressure_coating_pareto_diagnostic"]
    artifact = {
        "terminal": {
            "run_metadata": {"pressure_coating_pareto_diagnostic": coating},
        },
        "timesteps": [],
    }

    html = _render_panel(artifact)["html"]
    coating_region = _region(
        html,
        '<div class="sec-p3-coating-status">',
        "<h3>Wall deposit by species</h3>",
    )
    species_table = _region(
        html,
        "<h3>Wall deposit by species</h3>",
        '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
    )

    assert coating["status"] == "ok"
    assert "diagnostic_only" not in coating and "authoritative" not in coating
    assert "Coating replay diagnostic status" in coating_region
    assert "Diagnostic-only surface (schema)" in coating_region
    assert "Status</b> ok" in coating_region
    assert "authority" not in coating_region.lower()
    assert "Diagnostic cumulative · kg" in species_table
    assert "Replay cumulative" not in species_table

    for species in ("Si", "SiO2"):
        assert species not in coating["by_species"]
        emitted = coating["current"]["wall_deposit_cumulative_kg_by_species"][species]
        expected = re.sub(r"e([+-])0+(\d+)", r"e\1\2", f"{emitted:.2e}") + " kg"
        cells = _cell_texts(_row_with(species_table, f'title="{species}"'))
        assert cells[3] == expected
        assert cells[4] == "Per-species status not emitted"


def test_p3_mixed_partial_sources_rank_each_species_by_best_emitted_value() -> None:
    artifact = _artifact()
    artifact["terminal"] = {
        "final": {"wall_deposit_by_species_kg": {"SiO": 1.0}},
        "run_metadata": {
            "pressure_coating_pareto_diagnostic": {
                "current": {"wall_deposit_cumulative_kg_by_species": {"Fe": 100.0}},
            }
        },
    }

    species_table = _region(
        _render_panel(artifact)["html"],
        "<h3>Wall deposit by species</h3>",
        '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
    )

    assert species_table.index('title="Fe"') < species_table.index('title="SiO"')
    assert _cell_texts(_row_with(species_table, 'title="Fe"'))[1:] == [
        "not emitted", "not emitted", "100 kg", "Per-species status not emitted",
    ]
    assert _cell_texts(_row_with(species_table, 'title="SiO"'))[1:] == [
        "1 kg", "not emitted", "not emitted", "Per-species status not emitted",
    ]


def test_p3_raw_render_and_timestep_guards_cover_malformed_inputs() -> None:
    malformed_artifacts: tuple[object, ...] = (
        None,
        {},
        [],
        {"terminal": None},
        {"terminal": {}, "timesteps": None},
        {"terminal": {}, "timesteps": "malformed"},
    )
    for artifact in malformed_artifacts:
        rendered = _render_panel(artifact, timestep_index=0)
        assert "Per-species wall deposits pending" in rendered["html"]
        assert "Hourly coating telemetry pending" in rendered["hourly"]
        assert "Selected hour · not emitted" in rendered["hourly"]

    valid = _artifact()
    valid["timesteps"] = [{"hour": 1, "summary": {}}]
    for index in (-1, 1, 99):
        hourly = _render_panel(valid, timestep_index=index)["hourly"]
        assert "Hourly coating telemetry pending" in hourly
        assert "Selected hour · not emitted" in hourly

    missing_target = _render_panel(valid, timestep_index=0, target_present=False)
    assert "Hourly coating telemetry pending" in missing_target["html"]
    assert missing_target["hourly"] == ""


def test_p3_wall_lifetime_present_path_surfaces_payload_without_viewer_verdict() -> None:
    artifact = _artifact()
    artifact["terminal"]["wall_lifetime"] = {
        "status": "ok",
        "authoritative": False,
        "diagnostic_only": True,
        "campaigns_to_resinter": 5.0,
        "notes": "model <draft>",
        "nested_evidence": {"ignored": True},
    }

    lifetime = _region(
        _render_panel(artifact)["html"],
        '<div class="sec-p3-lifetime">',
        "</div>",
    )

    assert "Wall lifetime diagnostic emitted" in lifetime
    assert "No viewer-derived pass/fail verdict" in lifetime
    assert "Status</b> ok" in lifetime
    assert "Authoritative</b> no" in lifetime
    assert "Diagnostic only</b> yes" in lifetime
    assert "Campaigns to resinter</b> 5" in lifetime
    assert "Notes</b> model &lt;draft&gt;" in lifetime
    assert "Nested evidence</b> emitted (object)" in lifetime
    # Emitting payload numbers is required; inventing a gate chip is still forbidden.
    assert "CLEAR" not in lifetime
    assert "ruined" not in lifetime
