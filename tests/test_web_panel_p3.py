from __future__ import annotations

from html import unescape
import json
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1] / "web" / "report_viewer"


def _render_panel(
    artifact: object,
    *,
    timestep_index: int | None = None,
    target_present: bool = True,
    species_color: str | None = None,
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
if (process.argv[7] !== "none") {
  context.ReportLabels = Object.freeze({
    ...context.ReportLabels,
    speciesColor: () => process.argv[7],
  });
}
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
            "none" if species_color is None else species_color,
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


def _text(rendered: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", rendered)).split())


def _header_texts(rendered: str) -> list[str]:
    match = re.search(r"<thead>(.*?)</thead>", rendered)
    assert match is not None
    return _cell_texts(match.group(1))


def _table_body_rows(rendered: str) -> list[str]:
    match = re.search(r"<tbody>(.*?)</tbody>", rendered)
    assert match is not None
    return re.findall(r"<tr(?:\s[^>]*)?>.*?</tr>", match.group(1))


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

    rendered = _render_panel(artifact, timestep_index=0, species_color="#123abc")
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
    assert 'style="background:#123abc"' in sio_row
    assert 'style="background:#123abc"' in fe_row
    assert _cell_texts(_row_with(segment_region, 'title="SiO"')) == [
        "Wall deposit · stage 0→1", "SiO", "0.2 kg",
    ]
    assert _cell_texts(_row_with(segment_region, 'title="Na"')) == [
        "Wall deposit · stage 0→1", "Na", "0.03 kg",
    ]
    assert _cell_texts(_row_with(segment_region, 'title="Fe"')) == [
        "Wall deposit · stage 1→2", "Fe", "0.01 kg",
    ]
    assert _header_texts(segment_region) == [
        "Wall segment", "Species", "Terminal deposited mass · kg",
    ]
    assert len(_table_body_rows(segment_region)) == 3
    assert "Regime</b> viscous" in html and "Kn</b> 0.0038" in html
    assert "SiO watch species" in html
    assert "Pump outlet</b> not applicable until p0" in html
    assert "Selected hour · 7" in rendered["hourly"]
    assert _cell_texts(_row_with(rendered["hourly"], 'title="SiO"')) == [
        "Wall deposit · stage 0→1", "SiO", "0.004 kg", "0.25 kg",
    ]
    assert _cell_texts(_row_with(rendered["hourly"], 'title="Na"')) == [
        "Wall deposit · stage 0→1", "Na", "0.001 kg", "0.03 kg",
    ]
    assert _cell_texts(_row_with(rendered["hourly"], 'title="Fe"')) == [
        "Wall deposit · stage 1→2", "Fe", "0.002 kg", "0.01 kg",
    ]
    assert _header_texts(rendered["hourly"]) == [
        "Wall segment", "Species", "Deposit this hour · kg", "Running wall load · kg",
    ]
    assert "kg/hour" not in rendered["hourly"] and "kg/hr" not in rendered["hourly"]
    assert "Terminal aggregate · kg" in species_table
    assert "Current coating flux · kg/hr" in species_table
    assert "Diagnostic cumulative · kg" in species_table
    assert "mol" not in species_table
    assert "mol" not in html and "mol" not in rendered["hourly"]

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
        "<summary>Pump-outlet context</summary>",
        "</details>",
    )
    assert _cell_texts(_row_with(pump_region, 'title="Fe"')) == ["Fe", "0.02 kg"]
    assert _cell_texts(_row_with(pump_region, 'title="SiO"')) == ["SiO", "0 kg"]
    assert _header_texts(pump_region) == ["Species", "Pump outlet · kg"]


def test_p3_absent_fields_stay_pending_without_coating_verdict() -> None:
    html = _render_panel(_artifact())["html"]

    assert "Per-species wall deposits pending" in html
    assert "Per-segment deposits pending" in html
    assert "Transport context pending" in html
    assert "Wall lifetime not assessed" in html
    lifetime_region = _region(html, '<div class="sec-p3-lifetime">', "</div>")
    assert "no viewer pass/fail verdict is issued" in lifetime_region
    assert all(token not in html for token in ("CLEAR", "ruined", "campaigns_to_resinter"))
    assert "0 kg" not in html

    for malformed_lifetime in (None, "unknown", []):
        artifact = _artifact()
        artifact["terminal"]["wall_lifetime"] = malformed_lifetime
        malformed_html = _render_panel(artifact)["html"]
        assert "Wall lifetime malformed" in malformed_html
        assert "Wall lifetime diagnostic emitted" not in malformed_html
        assert all(token not in malformed_html for token in ("CLEAR", "ruined", "campaigns_to_resinter"))

    empty_lifetime = _artifact()
    empty_lifetime["terminal"]["wall_lifetime"] = {}
    empty_lifetime_html = _render_panel(empty_lifetime)["html"]
    assert "Wall lifetime empty" in empty_lifetime_html
    assert "Wall lifetime malformed" not in empty_lifetime_html
    assert all(token not in empty_lifetime_html for token in ("CLEAR", "ruined", "campaigns_to_resinter"))

    sparse_knudsen = _artifact()
    sparse_knudsen["terminal"] = {
        "run_metadata": {"knudsen_regime_diagnostic": {}},
    }
    sparse_html = _render_panel(sparse_knudsen)["html"]
    assert "Transport context empty" in sparse_html

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

    for kn_value, expected in (("", "Kn</b> empty"), (None, "Kn</b> malformed (null)")):
        scalar_knudsen = _artifact()
        scalar_knudsen["terminal"] = {
            "run_metadata": {
                "knudsen_regime_diagnostic": {"knudsen_number": kn_value},
            },
        }
        scalar_transport = _region(
            _render_panel(scalar_knudsen)["html"],
            "<span>Transport context</span>",
            '<div class="sec-p3-coating-status">',
        )
        assert expected in scalar_transport

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
                "status": "unavailable",
                "authoritative": False,
                "diagnostic_only": True,
                "extrapolation": True,
                "high_uncertainty": True,
                "source": "model <draft>",
                "reference": "reference <draft>",
                "skip_reason": "coverage <missing>",
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
                    "severity": "warning",
                    "code": "wall_deposit_surface_geometry_provenance",
                    "usage": ["stage_area_m2_by_stage", "coating_lifespan"],
                    "message": 'geometry "not certified"',
                    "stage_area_ratio_provenance_by_stage": {
                        "stage_0_to_stage_1": {
                            "stage": "stage_0_to_stage_1",
                            "ratio": 0,
                            "status": "provisional",
                            "output_status": "status_bearing",
                            "source_class": "engineering-default",
                            "source": "engineering-default: baffled <throat>",
                            "usage": "condensation_surface_area",
                        },
                    },
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
    geometry_region = _region(
        html,
        '<div class="sec-p3-geometry">',
        '<div class="sec-p3-coating-status">',
    )

    assert "Coating replay diagnostic status" in coating_region
    assert "Diagnostic-only replay surface" in coating_region
    assert "status reports diagnostic availability" in coating_region
    assert "status is operational" not in coating_region
    assert "Status</b> unavailable" in coating_region
    assert "Authoritative</b> no" in coating_region
    assert "Diagnostic only</b> yes" in coating_region
    assert "Extrapolation</b> yes" in coating_region
    assert "High uncertainty</b> yes" in coating_region
    assert "Source</b> model &lt;draft&gt;" in coating_region
    assert "Reference</b> reference &lt;draft&gt;" in coating_region
    assert "Skip reason</b> coverage &lt;missing&gt;" in coating_region
    assert "Reason</b> coverage &lt;missing&gt;" in species_row
    assert "Source class</b> engineering default" in geometry_region
    assert "Provisional</b> yes" in geometry_region
    assert "Output status</b> status bearing" in geometry_region
    assert "Severity</b> warning" in geometry_region
    assert "Code</b> wall_deposit_surface_geometry_provenance" in geometry_region
    assert "Usage</b> stage_area_m2_by_stage, coating_lifespan" in geometry_region
    stage_row = _row_with(geometry_region, "stage_0_to_stage_1")
    assert _cell_texts(stage_row) == [
        "stage_0_to_stage_1",
        "stage_0_to_stage_1",
        "0",
        "provisional",
        "status_bearing",
        "engineering-default",
        "engineering-default: baffled <throat>",
        "condensation_surface_area",
    ]
    assert _species_text_and_title(species_row) == ("SiO<script>", "SiO<script>")
    assert 'title="geometry &quot;not certified&quot;"' in geometry_region
    assert "<script>" not in species_row and 'geometry "not certified"' not in geometry_region
    assert "baffled <throat>" not in geometry_region


@pytest.mark.parametrize("status, leaf", [
    ("unavailable", 0), ("UNAVAILABLE", 12.5), ("unavailable", None),
    ("unavailable", "absent"), (None, None),
])
def test_p3_unavailable_diagnostics_never_claim_quantities(status, leaf) -> None:
    entry = {"reason": "species_absent_from_latest_evaporation_series_diagnostic"}
    if status is not None:
        entry["status"] = status
    if leaf != "absent":
        entry.update(current_wall_deposit_flux_kg_hr=leaf, cumulative_wall_deposit_kg=leaf)
    artifact = {"terminal": {
        "final": {"wall_deposit_by_species_kg": {"Fe": 2.5}},
        "run_metadata": {"pressure_coating_pareto_diagnostic": {
            "current": {
                "wall_deposit_flux_kg_hr_by_species": {"Fe": 19, "SiO2": 0.125},
                "wall_deposit_cumulative_kg_by_species": {"Fe": 23, "SiO2": 3.5},
            },
            "by_species": {
                "Fe": entry,
                "Si": {"status": "ok", "current_wall_deposit_flux_kg_hr": 0,
                       "cumulative_wall_deposit_kg": 4.5},
            },
        }},
    }}
    html = _render_panel(artifact)["html"]
    cells = _cell_texts(_row_with(html, 'title="Fe"'))
    assert cells[1:4] == ["2.5 kg", "unavailable", "unavailable"]
    assert "Reason species absent from latest evaporation series diagnostic" in cells[4]
    assert _cell_texts(_row_with(html, 'title="Si"'))[2:4] == ["0 kg/hr", "4.5 kg"]
    assert _cell_texts(_row_with(html, 'title="SiO2"'))[2:4] == ["0.125 kg/hr", "3.5 kg"]


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
    assert "0.271 kg" not in species_row
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
    assert "0.333 kg" not in reverse_row
    assert "0.8 kg" not in reverse_row
    assert "0.333 kg" in reverse_render["hourly"]
    assert "kg/hour" not in reverse_render["hourly"]

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
    assert "Diagnostic-only replay surface" in coating_region
    assert "status reports diagnostic availability" in coating_region
    assert "status is operational" not in coating_region
    assert "Status</b> ok" in coating_region
    assert "authority" not in coating_region.lower()
    assert "Diagnostic cumulative · kg" in species_table
    assert "Replay cumulative" not in species_table

    for species in ("Si", "SiO2"):
        assert species not in coating["by_species"]
        emitted = coating["current"]["wall_deposit_cumulative_kg_by_species"][species]
        # Mirror labels.js fmtNum: scientific notation only when
        # abs(value) < 1e-3; otherwise locale formatting with four
        # significant digits. Derivation on this target sample:
        #   Si   = 16.305254683323845 → |x| >= 1e-3 → 16.31 kg
        #   SiO2 = 34.88227228548973  → |x| >= 1e-3 → 34.88 kg
        # Source-sample test always expected `{emitted:.2e}` (`1.63e+1 kg`)
        # because that blob's Si sat below the 1e-3 threshold. Do not
        # force scientific notation or replace the target sample.
        if abs(emitted) < 1e-3:
            expected = re.sub(r"e([+-])0+(\d+)", r"e\1\2", f"{emitted:.2e}") + " kg"
        else:
            expected = f"{emitted:.4g} kg"
        cells = _cell_texts(_row_with(species_table, f'title="{species}"'))
        assert cells[3] == expected
        assert cells[4] == "Per-species status not emitted"


def test_p3_species_ranking_uses_one_table_wide_emitted_metric() -> None:
    def species_table(artifact: object) -> str:
        return _region(
            _render_panel(artifact)["html"],
            "<h3>Wall deposit by species</h3>",
            '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
        )

    terminal_first = {
        "terminal": {
            "final": {"wall_deposit_by_species_kg": {"SiO": 1.0}},
            "run_metadata": {
                "pressure_coating_pareto_diagnostic": {
                    "current": {"wall_deposit_cumulative_kg_by_species": {"Fe": 100.0}},
                },
            },
        },
        "timesteps": [],
    }
    terminal_table = species_table(terminal_first)
    assert terminal_table.index('title="SiO"') < terminal_table.index('title="Fe"')
    assert _cell_texts(_row_with(terminal_table, 'title="SiO"'))[1:] == [
        "1 kg", "not emitted", "not emitted", "Per-species status not emitted",
    ]
    assert _cell_texts(_row_with(terminal_table, 'title="Fe"'))[1:] == [
        "not emitted", "not emitted", "100 kg", "Per-species status not emitted",
    ]

    reverse_magnitude = {
        "terminal": {
            "final": {"wall_deposit_by_species_kg": {"SiO": 100.0}},
            "run_metadata": {
                "pressure_coating_pareto_diagnostic": {
                    "current": {"wall_deposit_cumulative_kg_by_species": {"Fe": 1.0}},
                },
            },
        },
        "timesteps": [],
    }
    reverse_table = species_table(reverse_magnitude)
    assert reverse_table.index('title="SiO"') < reverse_table.index('title="Fe"')

    cumulative_over_flux = {
        "terminal": {
            "run_metadata": {
                "pressure_coating_pareto_diagnostic": {
                    "current": {
                        "wall_deposit_cumulative_kg_by_species": {"SiO": 0.01},
                        "wall_deposit_flux_kg_hr_by_species": {"Fe": 100.0},
                    },
                },
            },
        },
        "timesteps": [],
    }
    cumulative_table = species_table(cumulative_over_flux)
    assert cumulative_table.index('title="SiO"') < cumulative_table.index('title="Fe"')
    assert _cell_texts(_row_with(cumulative_table, 'title="SiO"'))[1:4] == [
        "not emitted", "not emitted", "0.01 kg",
    ]
    assert _cell_texts(_row_with(cumulative_table, 'title="Fe"'))[1:4] == [
        "not emitted", "100 kg/hr", "not emitted",
    ]

    flux_only = {
        "terminal": {
            "run_metadata": {
                "pressure_coating_pareto_diagnostic": {
                    "current": {
                        "wall_deposit_flux_kg_hr_by_species": {"SiO": 100.0, "Fe": 1.0},
                    },
                },
            },
        },
        "timesteps": [],
    }
    flux_table = species_table(flux_only)
    assert flux_table.index('title="SiO"') < flux_table.index('title="Fe"')
    assert _cell_texts(_row_with(flux_table, 'title="SiO"'))[1:4] == [
        "not emitted", "100 kg/hr", "not emitted",
    ]
    assert _cell_texts(_row_with(flux_table, 'title="Fe"'))[1:4] == [
        "not emitted", "1 kg/hr", "not emitted",
    ]


def test_p3_raw_render_and_timestep_guards_cover_malformed_inputs() -> None:
    malformed_artifacts: tuple[tuple[object, str], ...] = (
        (None, "Hourly coating telemetry pending"),
        ({}, "Hourly coating telemetry pending"),
        ([], "Timestep list malformed"),
        ({"terminal": None}, "Hourly coating telemetry pending"),
        ({"terminal": {}, "timesteps": None}, "Timestep list malformed"),
        ({"terminal": {}, "timesteps": "malformed"}, "Timestep list malformed"),
    )
    for artifact, expected_hourly_state in malformed_artifacts:
        rendered = _render_panel(artifact, timestep_index=0)
        assert "Per-species wall deposits pending" in rendered["html"]
        assert expected_hourly_state in rendered["hourly"]
        assert "Selected hour · not emitted" in rendered["hourly"]

    valid = _artifact()
    valid["timesteps"] = [{"hour": 1, "summary": {}}]
    for index in (-1, 1, 99):
        hourly = _render_panel(valid, timestep_index=index)["hourly"]
        assert "Hourly coating telemetry pending" in hourly
        assert "Selected hour · not emitted" in hourly

    empty_timesteps = _render_panel(_artifact(), timestep_index=0)["hourly"]
    assert "Timestep list empty" in empty_timesteps
    assert "Selected hour · not emitted" in empty_timesteps

    missing_target = _render_panel(valid, timestep_index=0, target_present=False)
    assert "Selected-hour summary empty" in missing_target["html"]
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

    html = _render_panel(artifact)["html"]
    lifetime = _region(
        html,
        '<div class="sec-p3-lifetime">',
        "</div></section>",
    )

    assert _text(lifetime) == (
        "Wall lifetime diagnostic emitted No viewer-derived pass/fail verdict. "
        "Status ok Authoritative no Diagnostic only yes Campaigns to resinter 5 "
        "Nested evidence emitted (object) Notes model <draft>"
    )
    assert all(token not in html for token in ("CLEAR", "ruined", "campaigns_to_resinter"))


def test_p3_container_states_stay_distinct_in_routing_and_context_regions() -> None:
    def source_region(artifact: object) -> str:
        return _region(
            _render_panel(artifact)["html"],
            '<p class="sub">',
            '<div class="sec-p3-context">',
        )

    assert "Report artifact</b> not emitted" in source_region(None)
    assert "Report artifact</b> empty" in source_region({})
    assert "Report artifact</b> malformed (array)" in source_region([])

    missing = source_region({"terminal": {"other": True}})
    assert "Final summary</b> not emitted" in missing
    assert "Run metadata</b> not emitted" in missing
    assert "Timesteps</b> not emitted" in missing

    empty = source_region({"terminal": {"final": {}, "run_metadata": {}}, "timesteps": []})
    assert "Final summary</b> empty" in empty
    assert "Run metadata</b> empty" in empty
    assert "Timesteps</b> empty" in empty

    malformed = source_region({"terminal": {"final": [], "run_metadata": []}, "timesteps": {}})
    assert "Final summary</b> malformed (array)" in malformed
    assert "Run metadata</b> malformed (array)" in malformed
    assert "Timesteps</b> malformed (object)" in malformed

    for coating_value, expected in (
        (None, "Coating diagnostic</b> not emitted"),
        ({}, "Coating diagnostic</b> empty"),
        ([], "Coating diagnostic</b> malformed (array)"),
    ):
        metadata = {} if coating_value is None else {
            "pressure_coating_pareto_diagnostic": coating_value,
        }
        artifact = {"terminal": {"run_metadata": metadata}, "timesteps": []}
        coating_region = _region(
            _render_panel(artifact)["html"],
            '<div class="sec-p3-coating-status">',
            "<h3>Wall deposit by species</h3>",
        )
        assert expected in coating_region

    malformed_status = {
        "terminal": {
            "run_metadata": {
                "pressure_coating_pareto_diagnostic": {"status": {}},
            },
        },
        "timesteps": [],
    }
    malformed_status_region = _region(
        _render_panel(malformed_status)["html"],
        '<div class="sec-p3-coating-status">',
        "<h3>Wall deposit by species</h3>",
    )
    assert "Status</b> malformed (object)" in malformed_status_region

    emitted_without_status = {
        "terminal": {
            "run_metadata": {
                "pressure_coating_pareto_diagnostic": {
                    "by_species": {"SiO": {"status": "ok"}},
                },
            },
        },
        "timesteps": [],
    }
    emitted_without_status_region = _region(
        _render_panel(emitted_without_status)["html"],
        '<div class="sec-p3-coating-status">',
        "<h3>Wall deposit by species</h3>",
    )
    assert "Diagnostic status</b> not emitted" in emitted_without_status_region
    assert "<b>Diagnostic</b> not emitted" not in emitted_without_status_region

    for knudsen_value, expected in (
        (None, "Transport context pending"),
        ({}, "Transport context empty"),
        ([], "Transport context malformed"),
    ):
        metadata = {} if knudsen_value is None else {
            "knudsen_regime_diagnostic": knudsen_value,
        }
        artifact = {"terminal": {"run_metadata": metadata}, "timesteps": []}
        transport = _region(
            _render_panel(artifact)["html"],
            "<span>Transport context</span>",
            '<div class="sec-p3-coating-status">',
        )
        assert expected in transport

    for geometry_value, expected in (
        (None, None),
        ({}, "Geometry notice</b> empty"),
        ([], "Geometry notice</b> malformed (array)"),
        ({"unrecognized": "value"}, "Geometry notice</b> emitted (no supported fields)"),
    ):
        knudsen = {"status": "ok"}
        if geometry_value is not None:
            knudsen["stage_area_geometry_provenance_notice"] = geometry_value
        artifact = {
            "terminal": {"run_metadata": {"knudsen_regime_diagnostic": knudsen}},
            "timesteps": [],
        }
        transport = _region(
            _render_panel(artifact)["html"],
            "<span>Transport context</span>",
            '<div class="sec-p3-coating-status">',
        )
        if expected is None:
            assert "Coating geometry provenance" not in transport
        else:
            assert expected in transport

    unsupported_knudsen = {
        "terminal": {
            "run_metadata": {
                "knudsen_regime_diagnostic": {"schema_version": "future-v1"},
            },
        },
        "timesteps": [],
    }
    unsupported_transport = _region(
        _render_panel(unsupported_knudsen)["html"],
        "<span>Transport context</span>",
        '<div class="sec-p3-coating-status">',
    )
    assert "Transport context emitted" in unsupported_transport
    assert "no P3-supported context fields" in unsupported_transport
    assert "Transport context pending" not in unsupported_transport


def test_p3_nested_map_states_are_visible_without_dropping_valid_siblings() -> None:
    def species_region(coating: object) -> str:
        artifact = {
            "terminal": {
                "final": {"wall_deposit_by_species_kg": {"SiO": 1.0}},
                "run_metadata": {"pressure_coating_pareto_diagnostic": coating},
            },
            "timesteps": [],
        }
        return _region(
            _render_panel(artifact)["html"],
            "<h3>Wall deposit by species</h3>",
            '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
        )

    omitted_entry = species_region({"by_species": {}})
    assert "Per-species diagnostic map empty" in omitted_entry
    assert _cell_texts(_row_with(omitted_entry, 'title="SiO"'))[4] == (
        "Per-species status not emitted"
    )

    malformed_parent = species_region({"by_species": []})
    assert "Per-species diagnostic map malformed" in malformed_parent
    assert "malformed (array)" in malformed_parent

    empty_entry = species_region({"by_species": {"SiO": {}}})
    assert _cell_texts(_row_with(empty_entry, 'title="SiO"'))[4] == (
        "Per-species entry empty"
    )

    malformed_entry = species_region({"by_species": {"SiO": []}})
    assert _cell_texts(_row_with(malformed_entry, 'title="SiO"'))[4] == (
        "Per-species entry malformed (array)"
    )

    current_empty = species_region({"by_species": {"SiO": {"status": "ok"}}, "current": {}})
    assert "Coating current map empty" in current_empty
    current_malformed = species_region({"by_species": {"SiO": {"status": "ok"}}, "current": []})
    assert "Coating current map malformed" in current_malformed
    assert "malformed (array)" in current_malformed

    nested_maps = species_region({
        "by_species": {"SiO": {"status": "ok"}},
        "current": {
            "wall_deposit_flux_kg_hr_by_species": {},
            "wall_deposit_cumulative_kg_by_species": [],
        },
    })
    assert "Current coating flux map empty" in nested_maps
    assert "Current coating cumulative map malformed" in nested_maps
    assert "malformed (array)" in nested_maps

    segment_artifact = {
        "terminal": {
            "final": {
                "deposit_by_surface_species_kg": {
                    "empty_segment": {},
                    "malformed_segment": [],
                    "stage_0_to_stage_1": {"SiO": 0.0},
                },
            },
        },
        "timesteps": [],
    }
    segment_region = _region(
        _render_panel(segment_artifact)["html"],
        "<summary>Per-wall-segment terminal breakdown</summary>",
        "</details>",
    )
    assert "Segment empty_segment empty" in segment_region
    assert "Segment malformed_segment malformed" in segment_region
    assert "malformed (array)" in segment_region
    assert _cell_texts(_row_with(segment_region, 'title="SiO"')) == [
        "Wall deposit · stage 0→1", "SiO", "0 kg",
    ]


def test_p3_hourly_container_states_and_partial_histories_never_derive_values() -> None:
    def hourly(timestep: object) -> str:
        artifact = {"terminal": {}, "timesteps": [timestep]}
        return _render_panel(artifact, timestep_index=0)["hourly"]

    assert "Hourly coating telemetry pending" in hourly({"hour": 1})
    assert "Selected-hour summary empty" in hourly({"hour": 1, "summary": {}})
    assert "Selected-hour summary malformed" in hourly({"hour": 1, "summary": []})

    empty_map = hourly({"hour": 1, "summary": {"wall_deposit_delta_kg": {}}})
    assert "Hourly deposit map empty" in empty_map
    malformed_map = hourly({"hour": 1, "summary": {"wall_deposit_delta_kg": []}})
    assert "Hourly deposit map malformed" in malformed_map
    assert "malformed (array)" in malformed_map

    malformed_child = hourly({
        "hour": 1,
        "summary": {
            "wall_deposit_delta_kg": {"stage_0_to_stage_1": []},
            "wall_deposit_cumulative_kg": {"stage_0_to_stage_1": {"SiO": 0.5}},
        },
    })
    malformed_row = _row_with(malformed_child, 'title="SiO"')
    assert _cell_texts(malformed_row) == [
        "Wall deposit · stage 0→1", "SiO", "malformed (array)", "0.5 kg",
    ]
    assert "Hourly segment stage_0_to_stage_1 malformed" in malformed_child

    empty_child = hourly({
        "hour": 1,
        "summary": {
            "wall_deposit_delta_kg": {"stage_0_to_stage_1": {}},
            "wall_deposit_cumulative_kg": {"stage_0_to_stage_1": {"SiO": 0.5}},
        },
    })
    assert _cell_texts(_row_with(empty_child, 'title="SiO"'))[2:] == [
        "not emitted", "0.5 kg",
    ]
    assert "Hourly segment stage_0_to_stage_1 empty" in empty_child

    for hour_value, expected in (
        (None, "Selected hour · malformed (null)"),
        ("", "Selected hour · empty"),
        (0, "Selected hour · 0"),
        ({}, "Selected hour · malformed (object)"),
    ):
        assert expected in hourly({"hour": hour_value, "summary": {}})
    assert "Selected hour · not emitted" in hourly({"summary": {}})

    delta_history = {
        "terminal": {
            "run_metadata": {
                "pressure_coating_pareto_diagnostic": {
                    "by_species": {"SiO": {"status": "ok"}},
                },
            },
        },
        "timesteps": [
            {"hour": 1, "summary": {"wall_deposit_delta_kg": {"seg": {"SiO": 0.111}}}},
            {"hour": 2, "summary": {"wall_deposit_delta_kg": {"seg": {"SiO": 0.222}}}},
        ],
    }
    delta_render = _render_panel(delta_history, timestep_index=1)
    delta_species = _region(
        delta_render["html"],
        "<h3>Wall deposit by species</h3>",
        '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
    )
    assert _cell_texts(_row_with(delta_species, 'title="SiO"'))[1:] == [
        "not emitted", "not emitted", "not emitted", "Status ok",
    ]
    assert _cell_texts(_row_with(delta_render["hourly"], 'title="SiO"'))[2:] == [
        "0.222 kg", "not emitted",
    ]
    assert "0.333 kg" not in delta_species and "0.333 kg" not in delta_render["hourly"]
    assert "kg/hour" not in delta_render["hourly"] and "kg/hr" not in delta_render["hourly"]

    cumulative_history = {
        "terminal": {
            "run_metadata": {
                "pressure_coating_pareto_diagnostic": {
                    "by_species": {"SiO": {"status": "ok"}},
                },
            },
        },
        "timesteps": [
            {"hour": 1, "summary": {"wall_deposit_cumulative_kg": {"seg": {"SiO": 0.5}}}},
            {"hour": 2, "summary": {"wall_deposit_cumulative_kg": {"seg": {"SiO": 0.8}}}},
        ],
    }
    cumulative_render = _render_panel(cumulative_history, timestep_index=1)
    cumulative_species = _region(
        cumulative_render["html"],
        "<h3>Wall deposit by species</h3>",
        '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
    )
    assert _cell_texts(_row_with(cumulative_species, 'title="SiO"'))[1:] == [
        "not emitted", "not emitted", "not emitted", "Status ok",
    ]
    assert _cell_texts(_row_with(cumulative_render["hourly"], 'title="SiO"'))[2:] == [
        "not emitted", "0.8 kg",
    ]
    assert "0.3 kg" not in cumulative_species
    assert "0.3 kg" not in cumulative_render["hourly"]


def test_p3_mol_native_final_state_is_not_relabeled_as_kg_wall_deposit() -> None:
    """P2: mol-native final_state wall accounts must not become kg wall deposits."""
    artifact = _artifact()
    artifact["terminal"] = {
        "final": {},
        "final_state": {
            "process.wall_deposit_segment_stage_0_to_stage_1": {"SiO": 2.0},
        },
    }
    html = _render_panel(artifact)["html"]
    species_region = _region(
        html,
        "<h3>Wall deposit by species</h3>",
        '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
    )
    assert "Per-species wall deposits pending" in species_region
    assert "Terminal aggregate pending" in species_region
    assert "2 kg" not in species_region
    assert "2 mol" not in species_region
    assert "process.wall_deposit_segment" not in species_region


def test_p3_product_and_evolved_bins_do_not_backfill_wall_deposit_table() -> None:
    """P2: metal yields / evolved vapor must stay out of the wall-deposit table."""
    artifact = {
        "terminal": {"final": {}},
        "timesteps": [
            {
                "hour": 1,
                "summary": {
                    "metal_yields_kg": {"Fe": 1.0},
                    "vapor_species_kg_hr": {"SiO": 0.5},
                },
            }
        ],
    }
    rendered = _render_panel(artifact)
    species_region = _region(
        rendered["html"],
        "<h3>Wall deposit by species</h3>",
        '<details class="sec-p3-details"><summary>Per-wall-segment terminal breakdown',
    )
    assert "Per-species wall deposits pending" in species_region
    assert "1 kg" not in species_region
    assert "0.5 kg" not in species_region
    assert 'title="Fe"' not in species_region
    assert 'title="SiO"' not in species_region


def test_p3_hostile_segment_keys_escape_once_in_terminal_and_hourly() -> None:
    """P2: segment-key esc is a distinct sink from species-key esc."""
    hostile = "</th><img src=x onerror=alert(1)><th>"
    # Terminal segment map
    terminal_art = _artifact()
    terminal_art["terminal"] = {
        "final": {
            "deposit_by_surface_species_kg": {hostile: {"SiO": 1.0}},
        }
    }
    terminal_html = _render_panel(terminal_art)["html"]
    terminal_seg = _region(
        terminal_html,
        "<summary>Per-wall-segment terminal breakdown</summary>",
        "</details>",
    )
    assert "<img" not in terminal_seg
    assert "onerror=alert(1)" not in terminal_seg
    # accountLabel title-cases free-text segment tails before esc.
    assert (
        "Wall Deposit Segment &lt;/Th&gt;&lt;Img Src=X Onerror=Alert(1)&gt;&lt;Th&gt;"
        in terminal_seg
    )
    assert "&amp;lt;/Th&amp;gt;" not in terminal_seg

    # Hourly segment maps (distinct esc site at renderHourlySegments).
    hourly_art = {
        "terminal": {"final": {}},
        "timesteps": [
            {
                "hour": 3,
                "summary": {
                    "wall_deposit_delta_kg": {hostile: {"SiO": 0.2}},
                    "wall_deposit_cumulative_kg": {hostile: {"SiO": 0.4}},
                },
            }
        ],
    }
    hourly = _render_panel(hourly_art, timestep_index=0)["hourly"]
    assert "<img" not in hourly
    assert "onerror=alert(1)" not in hourly
    assert (
        "Wall Deposit Segment &lt;/Th&gt;&lt;Img Src=X Onerror=Alert(1)&gt;&lt;Th&gt;"
        in hourly
    )
    assert "&amp;lt;/Th&amp;gt;" not in hourly
