from __future__ import annotations

import json
from pathlib import Path
import subprocess


def _render_panel(artifact: object, *, species_colors: dict[str, str] | None = None) -> str:
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
process.stdout.write(panel.render(JSON.parse(process.argv[4]), [], [], {}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(root / "labels.js"),
            str(root / "panels/p4-stage-purity.js"),
            json.dumps(artifact),
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


def _emitted_totals_region(html: str) -> str:
    return _between(html, "<h4>Emitted totals <small>kg basis</small></h4>", "</dl>")


def _verdict_line(html: str) -> str:
    return _between(html, '<div class="sec-p4-verdict-line">', "</div>")


def _headline_value(html: str, label: str) -> str:
    return _between(html, f"<span>{label}</span><b>", "</b>")


def _complete_stage(**overrides: object) -> dict[str, object]:
    stage: dict[str, object] = {
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
        "verdict": "PURE",
        "warning": "",
        "activity": {"Fe": True, "Co": False},
    }
    stage.update(overrides)
    return stage


def _render_stage(stage: dict[str, object], **kwargs: object) -> str:
    return _render_panel(
        {"terminal": {"stage_purity": {"stage_1": stage}}},
        **kwargs,
    )


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
            "emitted map contains no measured species values</span>"
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
        "Backend-emitted stage mass, grade, activity, and verdict. "
        "Purity and totals are not recomputed in the viewer."
    )


def test_partial_total_stays_pending_with_component_ingredients() -> None:
    stage = _complete_stage()
    stage.pop("total_kg")
    html = _render_stage(stage)

    total = _headline_value(html, "Total stage mass")
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

    totals = _emitted_totals_region(html)
    assert "Pending · designated_kg not emitted" in totals
    assert "1.27 kg" not in totals


def test_partial_impurity_total_stays_pending_with_species_ingredients() -> None:
    stage = _complete_stage()
    stage.pop("impurity_kg")
    html = _render_stage(stage)

    totals = _emitted_totals_region(html)
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
