from __future__ import annotations

import json
from pathlib import Path
import subprocess


def _render_panel(artifact: dict) -> str:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const panel = context.ReportPanels.find((item) => item.id === "sec-p4-stage-purity");
process.stdout.write(panel.render(JSON.parse(process.argv[4]), [], [], {}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(root / "labels.js"),
            str(root / "panels/p4-stage-purity.js"),
            json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _complete_stage(**overrides) -> dict:
    stage = {
        "stage_number": 1,
        "label": "Iron condenser",
        "accepted_species": ["Fe", "Co", "Ni"],
        "designated_species_kg": {"Fe": 1.25},
        "coproduct_species_kg": {"Co": 0.02},
        "impurity_species_kg": {"SiO2": 0.001},
        "designated_kg": 1.27,
        "impurity_kg": 0.001,
        "total_kg": 1.271,
        "purity_fraction": 0.9992,
        "verdict": "MIXED",
        "warning": "Silica carryover observed.",
        "activity": {"Fe": True, "Co": True, "SiO2": False},
    }
    stage.update(overrides)
    return stage


def test_stage_purity_panel_renders_emitted_grade_breakdowns_and_activity() -> None:
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": _complete_stage()}}})

    assert 'id="sec-p4-stage-purity"' in html
    assert "Stage 1" in html
    assert "Iron condenser" in html
    assert "1.271 kg" in html
    assert "0.9992" in html
    assert "MIXED" in html
    assert "Designated + coproduct" in html
    assert "1.27 kg" in html
    assert "Designated species" in html
    assert "1.25 kg" in html
    assert "Coproduct species" in html
    assert "0.02 kg" in html
    assert "Impurity species" in html
    assert html.count("0.001 kg") == 2
    assert '<h4>Accepted species</h4><div class="sec-p4-species-row">' in html
    assert html.count(">Ni</span>") == 1
    assert "SiO₂" in html
    assert "ACTIVE" in html
    assert "IDLE" in html
    assert "Silica carryover observed." in html


def test_stage_purity_panel_keeps_absent_stage_map_pending() -> None:
    html = _render_panel({"terminal": {}})

    assert "terminal.stage_purity is not emitted" in html
    assert "Pending" in html
    assert "0 kg" not in html


def test_stage_purity_panel_keeps_sparse_and_empty_fields_pending() -> None:
    html = _render_panel({
        "terminal": {
            "stage_purity": {
                "stage_2": {
                    "accepted_species": [],
                    "designated_species_kg": {},
                    "coproduct_species_kg": {},
                    "impurity_species_kg": {},
                    "verdict": "PURE",
                }
            }
        }
    })

    assert "PURE" in html
    assert "stage label not emitted" in html
    assert "stage number not emitted" in html
    assert "total_kg not emitted" in html
    assert "purity_fraction not emitted" in html
    assert "designated_kg not emitted" in html
    assert "impurity_kg not emitted" in html
    assert "per-species activity not emitted" in html
    assert "warning not emitted" in html
    assert "No accepted species in emitted contract" in html
    assert "emitted map contains no measured species values" in html
    assert "0 kg" not in html


def test_stage_purity_panel_distinguishes_malformed_values_from_absence() -> None:
    html = _render_panel({
        "terminal": {
            "stage_purity": {
                "stage_bad": {
                    "stage_number": "one",
                    "label": [],
                    "accepted_species": {},
                    "designated_species_kg": [],
                    "coproduct_species_kg": None,
                    "impurity_species_kg": "bad",
                    "designated_kg": "bad",
                    "impurity_kg": [],
                    "total_kg": None,
                    "purity_fraction": "bad",
                    "verdict": [],
                    "warning": {},
                    "activity": [],
                }
            }
        }
    })

    assert "stage label is malformed" in html
    assert "stage number is malformed" in html
    assert "accepted species contract is malformed" in html
    assert html.count("map is malformed") == 4
    assert "designated_kg is malformed" in html
    assert "impurity_kg is malformed" in html
    assert "total_kg is malformed" in html
    assert "purity_fraction is malformed" in html
    assert "backend verdict is malformed" in html
    assert "warning is malformed" in html


def test_trace_and_idle_qualify_without_replacing_backend_verdict() -> None:
    stage = _complete_stage(
        designated_species_kg={"Fe": 0.004},
        coproduct_species_kg={},
        impurity_species_kg={"SiO2": 0.001},
        total_kg=0.005,
        designated_kg=0.004,
        impurity_kg=0.001,
        purity_fraction=0.8,
        verdict="CONTAMINATED",
        activity={"Fe": False, "Co": False},
    )
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": stage}}})

    assert "CONTAMINATED" in html
    assert "trace · &lt;0.01 kg total" in html
    assert "IDLE" in html
    assert "backend verdict not emitted" not in html


def test_stage_purity_panel_surfaces_authority_flags_and_escapes_artifact_text() -> None:
    stage = _complete_stage(
        label='<img src=x onerror="bad()">',
        warning="<script>bad()</script>",
        authoritative=False,
        diagnostic_only=True,
        high_uncertainty=True,
        status="provisional",
        source="<unsafe-source>",
        reference="W-A10",
    )
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": stage}}})

    assert "Authoritative" in html and "false" in html
    assert "Diagnostic only" in html and "true" in html
    assert "High uncertainty" in html
    assert "provisional" in html
    assert "&lt;unsafe-source&gt;" in html
    assert "&lt;script&gt;bad()&lt;/script&gt;" in html
    assert "<script>" not in html
    assert "<img" not in html
