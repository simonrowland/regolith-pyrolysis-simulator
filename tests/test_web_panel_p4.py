from __future__ import annotations

import json
from html import unescape
from pathlib import Path
import re
import subprocess

import pytest


_UNDEFINED = object()


def _render_panel(
    artifact: object = _UNDEFINED,
    *,
    species_colors: dict[str, str] | None = None,
) -> str:
    root = Path(__file__).resolve().parents[1] / "web/report_viewer"
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const colors = JSON.parse(process.argv[5]);
const defaultSpeciesColor = context.ReportLabels.speciesColor;
context.ReportLabels = Object.freeze({
  ...context.ReportLabels,
  speciesColor: (species) =>
    Object.prototype.hasOwnProperty.call(colors, species) ? colors[species] : defaultSpeciesColor(species),
});
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
            json.dumps(species_colors or {}),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _between(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0]


def _activity_region(html: str) -> str:
    return _between(html, "<h4>Activity</h4>", "</div>")


def _species_map_region(html: str, title: str) -> str:
    start = f'<div class="sec-p4-breakdown"><h4>{title} <small>kg basis</small></h4>'
    return _between(html, start, "</div>")


def _destination_totals_region(html: str) -> str:
    return _between(html, "<h4>Product-destination totals <small>kg basis</small></h4>", "</dl>")


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


def test_stage_mass_is_product_destination_classification_not_condenser_inventory() -> None:
    stage = _complete_stage(
        label="Fe Condenser", designated_species_kg={"Fe": 85.22329294683593},
        coproduct_species_kg={}, designated_kg=85.22329294683593,
        impurity_species_kg={"Al": 0.00176855144165}, impurity_kg=0.00176855144165,
        total_kg=85.22506149827758, purity_fraction=0.9999792473594084,
    )
    artifact = {
        "terminal": {
            "stage_purity": {"stage_1_fe_condenser": stage},
            "final_state": {"process.metal_phase_bottom_pool": {"Fe": 1525.994711609524}},
        },
        "timesteps": [{"summary": {"condensation_train_kg": {"Fe": 0.02739867051993343}}}],
    }
    html = _render_panel(artifact)
    visible = " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())
    assert "Terminal product-destination classification" in visible
    assert "Stage masses include stage-routed tap metal; not physical condenser inventory" in visible
    assert "Product destination · Stage 1" in visible
    assert "Fe Condenser" in visible
    assert "Total classified product mass 85.23 kg" in visible
    assert "Product-destination totals kg basis Designated + coproduct 85.22 kg" in visible
    for misleading in ("Condensation-train stage purity", "Total stage mass", "Collected-stage totals"):
        assert misleading not in visible
    assert "0.0274 kg" not in visible
    assert "1526" not in visible
    assert _headline_value(html, "Purity fraction") == "1"


def _render_stage(stage: dict[str, object], **kwargs: object) -> str:
    return _render_panel(
        {"terminal": {"stage_purity": {"stage_1": stage}}},
        **kwargs,
    )


def test_stage_purity_panel_renders_emitted_grade_breakdowns_and_activity() -> None:
    html = _render_panel({"terminal": {"stage_purity": {"stage_1": _complete_stage()}}})

    assert 'id="sec-p4-stage-purity"' in html
    assert "Terminal product-destination classification" in html
    assert "Condenser stage purity" not in html
    assert "Stage 1" in html
    assert "Iron condenser" in html
    assert _headline_value(html, "Total classified product mass") == "1.271 kg"
    assert _headline_value(html, "Purity fraction") == "0.9992"
    assert "MIXED" in html
    assert "trace · &lt;0.01 kg total" not in _verdict_line(html)
    assert "empty · 0 kg total" not in _verdict_line(html)
    destination_totals = _destination_totals_region(html)
    assert "Designated + coproduct" in destination_totals
    assert "1.27 kg" in destination_totals
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
    assert "Emitted map contains no species mass values" in html
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


def test_impossible_backend_verdict_is_malformed_not_rendered() -> None:
    html = _render_stage(_complete_stage(verdict="RECOVERED"))

    verdict_line = _verdict_line(html)
    assert "Pending · backend verdict is malformed" in verdict_line
    assert "RECOVERED" not in verdict_line


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
    assert _headline_value(html, "Total classified product mass") == "0 kg"


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

    total = _headline_value(html, "Total classified product mass")
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


def test_activity_rows_distinguish_boolean_missing_and_malformed_states() -> None:
    html = _render_stage(
        _complete_stage(
            accepted_species=["Fe", "Co", "Ni", "Mn"],
            activity={"Fe": True, "Co": False, "Ni": "yes"},
        )
    )

    activity = _activity_region(html)
    assert ">Fe</span><b>ACTIVE</b>" in activity
    assert ">Co</span><b>IDLE</b>" in activity
    assert (
        '>Ni</span><b>PENDING</b><span class="sec-p4-pending-value">'
        "activity state is malformed</span>"
        in activity
    )
    assert (
        '>Mn</span><b>PENDING</b><span class="sec-p4-pending-value">'
        "activity state not emitted</span>"
        in activity
    )


def test_empty_activity_map_is_empty_not_pending_without_contract_species() -> None:
    html = _render_stage(_complete_stage(accepted_species=[], activity={}))

    activity = _activity_region(html)
    assert (
        '<span class="sec-p4-empty-value">'
        "No species states in emitted activity map</span>"
        in activity
    )
    assert "Pending ·" not in activity


def test_emitted_empty_species_maps_are_empty_not_pending() -> None:
    html = _render_stage(
        _complete_stage(
            designated_species_kg={},
            coproduct_species_kg={},
            impurity_species_kg={},
        )
    )

    for title in ("Designated species", "Coproduct species", "Impurity species"):
        region = _species_map_region(html, title)
        assert (
            '<span class="sec-p4-empty-value">'
            "Emitted map contains no species mass values</span>"
            in region
        )
        assert "Pending ·" not in region


def test_species_colors_are_bound_to_shared_helper_output() -> None:
    html = _render_stage(
        _complete_stage(accepted_species=["Fe", "SiO2"]),
        species_colors={"Fe": "sentinel-fe", "SiO2": "sentinel-sio2"},
    )

    accepted = _between(html, "<h4>Accepted species</h4>", "</div>")
    assert 'style="--species-color:sentinel-fe"' in accepted
    assert 'style="--species-color:sentinel-sio2"' in accepted


def test_subtitle_binds_backend_emission_and_no_recompute_claims() -> None:
    html = _render_stage(_complete_stage())

    subtitle = _between(html, '<p class="sub">', "</p>")
    assert subtitle == (
        "Backend-emitted product-destination mass, grade, activity, and verdict. "
        "Stage masses include stage-routed tap metal; not physical condenser inventory. "
        "Purity and totals are not recomputed in the viewer."
    )


def test_partial_total_stays_pending_with_component_ingredients() -> None:
    stage = _complete_stage()
    stage.pop("total_kg")
    html = _render_stage(stage)

    total = _headline_value(html, "Total classified product mass")
    assert "Pending · total_kg not emitted" in total
    assert "1.271 kg" not in total


def test_partial_purity_stays_pending_with_ratio_ingredients() -> None:
    stage = _complete_stage(designated_kg=0.5, total_kg=1.0)
    stage.pop("purity_fraction")
    html = _render_stage(stage)

    purity = _headline_value(html, "Purity fraction")
    assert "Pending · purity_fraction not emitted" in purity
    assert "0.5" not in purity


def test_partial_designated_total_stays_pending_with_species_ingredients() -> None:
    stage = _complete_stage()
    stage.pop("designated_kg")
    html = _render_stage(stage)

    totals = _destination_totals_region(html)
    assert "Pending · designated_kg not emitted" in totals
    assert "1.27 kg" not in totals


def test_partial_impurity_total_stays_pending_with_species_ingredients() -> None:
    stage = _complete_stage()
    stage.pop("impurity_kg")
    html = _render_stage(stage)

    totals = _destination_totals_region(html)
    assert "Pending · impurity_kg not emitted" in totals
    assert "0.001 kg" not in totals


def test_partial_verdict_stays_pending_with_purity_ingredient() -> None:
    stage = _complete_stage()
    stage.pop("verdict")
    html = _render_stage(stage)

    verdict = _verdict_line(html)
    assert "Pending · backend verdict not emitted" in verdict
    assert "PURE" not in verdict
    assert "MIXED" not in verdict
    assert "CONTAMINATED" not in verdict
