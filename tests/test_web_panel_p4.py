from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


_UNDEFINED = object()


def _render_panel(artifact: object = _UNDEFINED) -> str:
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
const artifact = process.argv[4] === "__P4_UNDEFINED__" ? undefined : JSON.parse(process.argv[4]);
process.stdout.write(panel.render(artifact, [], [], {}));
"""
    payload = "__P4_UNDEFINED__" if artifact is _UNDEFINED else json.dumps(artifact)
    completed = subprocess.run(
        [
            "node",
            "-",
            str(root / "labels.js"),
            str(root / "panels/p4-stage-purity.js"),
            payload,
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _between(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0]


def _headline_value(html: str, label: str) -> str:
    return _between(html, f"<span>{label}</span><b>", "</b>")


def _verdict_line(html: str) -> str:
    return _between(html, '<div class="sec-p4-verdict-line">', "</div>")


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
    assert "Condensation-train stage purity" in html
    assert "Condenser stage purity" not in html
    assert "Stage 1" in html
    assert "Iron condenser" in html
    assert _headline_value(html, "Total stage mass") == "1.271 kg"
    assert _headline_value(html, "Purity fraction") == "0.9992"
    assert "MIXED" in html
    assert "trace · &lt;0.01 kg total" not in _verdict_line(html)
    assert "empty · 0 kg total" not in _verdict_line(html)
    assert "Designated + coproduct" in html
    assert "1.27 kg" in html
    assert "Designated species" in html
    assert "1.25 kg" in html
    assert "Coproduct species" in html
    assert "0.02 kg" in html
    assert "Impurity species" in html
    assert html.count("0.001 kg") == 2
    assert '<h4>Accepted species</h4><div class="sec-p4-species-row">' in html
    assert html.count(">Ni</span>") == 2
    assert ">Ni</span><b>PENDING</b>" in html
    assert "SiO₂" in html
    assert 'style="--species-color:#d95f02"' in html
    assert 'style="--species-color:#7570b3"' in html
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


def test_positive_trace_keeps_backend_verdict_without_stagewide_activity_badge() -> None:
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

    verdict_line = _verdict_line(html)
    assert "CONTAMINATED" in verdict_line
    assert "trace · &lt;0.01 kg total" in verdict_line
    assert "IDLE" not in verdict_line
    assert ">Fe</span><b>IDLE</b>" in html
    assert "backend verdict not emitted" not in html


def test_exact_zero_is_empty_not_trace() -> None:
    stage = _complete_stage(
        designated_species_kg={},
        coproduct_species_kg={},
        impurity_species_kg={},
        designated_kg=0,
        impurity_kg=0,
        total_kg=0,
        purity_fraction=1.0,
        verdict="PURE",
        activity={},
    )
    html = _render_panel({"terminal": {"stage_purity": {"stage_0": stage}}})

    verdict_line = _verdict_line(html)
    assert "empty · 0 kg total" in verdict_line
    assert "trace · &lt;0.01 kg total" not in verdict_line
    assert _headline_value(html, "Total stage mass") == "0 kg"


def test_sparse_activity_stays_per_species_without_stagewide_idle() -> None:
    stage = _complete_stage(
        accepted_species=["Cr", "Cr2O3", "CrO2", "Mn"],
        activity={"Cr": False},
    )
    html = _render_panel({"terminal": {"stage_purity": {"stage_2": stage}}})

    assert "IDLE" not in _verdict_line(html)
    assert ">Cr</span><b>IDLE</b>" in html
    assert ">Cr₂O₃</span><b>PENDING</b>" in html
    assert ">CrO₂</span><b>PENDING</b>" in html
    assert ">Mn</span><b>PENDING</b>" in html


def test_emitted_purity_wins_over_derivable_ratio() -> None:
    stage = _complete_stage(designated_kg=0.5, total_kg=1.0, purity_fraction=0.9)
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": stage}}})

    purity = _headline_value(html, "Purity fraction")
    assert purity == "0.9"
    assert "0.5" not in purity


def test_partial_purity_stays_pending_when_ratio_inputs_exist() -> None:
    stage = _complete_stage(designated_kg=0.5, total_kg=1.0)
    stage.pop("purity_fraction")
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": stage}}})

    purity = _headline_value(html, "Purity fraction")
    assert "Pending · purity_fraction not emitted" in purity
    assert "0.5" not in purity


def test_partial_total_stays_pending_when_species_maps_exist() -> None:
    stage = _complete_stage()
    stage.pop("total_kg")
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": stage}}})

    total = _headline_value(html, "Total stage mass")
    assert "Pending · total_kg not emitted" in total
    assert "1.271 kg" not in total


def test_malformed_species_masses_stay_pending_not_measured_zero() -> None:
    stage = _complete_stage(
        designated_species_kg={"Fe": None, "Ni": "NaN", "Co": {"kg": 0}},
        coproduct_species_kg={},
        impurity_species_kg={},
    )
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": stage}}})

    assert html.count("Pending · species mass is malformed") == 3
    assert ">Fe</span><b><span class=\"sec-p4-pending-value\">" in html
    assert ">Ni</span><b><span class=\"sec-p4-pending-value\">" in html
    assert ">Co</span><b><span class=\"sec-p4-pending-value\">" in html
    assert "0 kg" not in html


def test_stage_purity_panel_binds_authority_values_and_escapes_artifact_text_once() -> None:
    hostile_species = "<img src=x onerror=bad()>"
    stage = _complete_stage(
        label="Fe & Ni",
        accepted_species=[hostile_species],
        designated_species_kg={hostile_species: 1.25},
        warning="<script>bad()</script>",
        authoritative=False,
        diagnostic_only=True,
        extrapolation="bounded",
        high_uncertainty=True,
        status="provisional",
        source="<unsafe-source>",
        reference="W-A10",
        refusal_context={"guard": "<unsafe-guard>", "attempt": 2},
        skip_reason="range guard",
    )
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": stage}}})

    authority = _between(html, '<div class="sec-p4-authority">', "</div>")
    assert "<span><b>Authoritative</b> false</span>" in authority
    assert "<span><b>Diagnostic only</b> true</span>" in authority
    assert "<span><b>Extrapolation</b> bounded</span>" in authority
    assert "<span><b>High uncertainty</b> true</span>" in authority
    assert "<span><b>Status</b> provisional</span>" in authority
    assert "<span><b>Source</b> &lt;unsafe-source&gt;</span>" in authority
    assert "<span><b>Reference</b> W-A10</span>" in authority
    assert (
        "<span><b>Refusal context</b> "
        "{&quot;guard&quot;:&quot;&lt;unsafe-guard&gt;&quot;,&quot;attempt&quot;:2}</span>"
        in authority
    )
    assert "<span><b>Skip reason</b> range guard</span>" in authority
    assert "Fe &amp; Ni" in html
    assert "Fe &amp;amp; Ni" not in html
    assert "&lt;img src=x onerror=bad()&gt;" in html
    assert "&amp;lt;img" not in html
    assert "&lt;script&gt;bad()&lt;/script&gt;" in html
    assert "<script>" not in html
    assert "<img" not in html


@pytest.mark.parametrize(
    "artifact",
    [
        _UNDEFINED,
        None,
        7,
        "malformed",
        [],
        {},
        {"terminal": None},
        {"terminal": []},
        {"terminal": {}},
        {"terminal": {"stage_purity": []}},
        {"terminal": {"stage_purity": {}}},
        {"terminal": {"stage_purity": {"stage_bad": 7}}},
    ],
)
def test_malformed_outer_artifacts_render_p4_pending_without_throwing(artifact: object) -> None:
    html = _render_panel(artifact)

    assert 'id="sec-p4-stage-purity"' in html
    assert "Pending" in html
    assert "Panel failed to render" not in html
