from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p10-vapor-source.js"


def _render(report: object = ...):
    terminal = {} if report is ... else {"vapor_pressure_source_report": report}
    artifact = {"terminal": terminal}
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const panel = context.ReportPanels.find((entry) => entry.id === "sec-p10-vapor-source");
process.stdout.write(panel.render(JSON.parse(process.argv[4]), [], [], {}));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _complete_report() -> dict:
    return {
        "species": {
            "Na": "builtin_authoritative",
            "SiO2": "vaporock_backsolved_curve_fit:backsolved_vaporock_curve_fit",
        },
        "summary": {
            "builtin_authoritative": {"count": 1, "percentage": 50.0},
            "vaporock_backsolved_curve_fit:backsolved_vaporock_curve_fit": {
                "count": 1,
                "percentage": 50.0,
            },
        },
        "total_species": 2,
        "vapor_pressure_backend_status": "fallback",
        "vapor_pressure_backend_status_summary": {
            "fallback": {"count": 2, "percentage": 100.0}
        },
        "vapor_pressure_backend_status_reason": "vaporock_to_antoine_fallback",
        "vapor_pressure_fallback_source": "antoine_fallback_from_vaporock",
        "authoritative_for_requested_vapor_pressure": False,
    }


def test_present_report_renders_scalar_tokens_and_full_envelope() -> None:
    html = _render(_complete_report())
    summary_grid = html.split('class="sec-p10-summary-grid">', 1)[1].split(
        '<div class="sec-p10-species-block">', 1
    )[0]

    assert "Per-species source tokens" in html
    assert "SiO₂" in html
    assert "vaporock_backsolved_curve_fit:backsolved_vaporock_curve_fit" in html
    assert "Total species" in html and ">2<" in html
    assert "Source summary" in html
    assert "builtin_authoritative" in summary_grid
    assert "vaporock_backsolved_curve_fit:backsolved_vaporock_curve_fit" in summary_grid
    assert summary_grid.count(">1<") >= 2
    assert summary_grid.count(">50<") >= 2
    assert "Vapor-pressure backend status" in html and "fallback" in html
    assert "Backend-status summary" in html
    assert "vaporock_to_antoine_fallback" in html
    assert "antoine_fallback_from_vaporock" in html


def test_absent_report_is_explicitly_pending() -> None:
    html = _render()

    assert "Pending producer" in html
    assert "terminal vapor-pressure source report was not emitted" in html
    assert "No per-species sources emitted" not in html


def test_malformed_report_is_distinct_from_absent_report() -> None:
    for malformed, emitted_type in ((None, "null"), ([], "array"), ("report", "string")):
        html = _render(malformed)

        assert "Malformed emitted report" in html
        assert f"this artifact emitted {emitted_type}" in html
        assert "was not emitted" not in html


def test_empty_species_is_emitted_empty_not_default_authority() -> None:
    report = _complete_report()
    report["species"] = {}
    report["summary"] = {}
    report["total_species"] = 0
    html = _render(report)

    assert "No per-species sources emitted" in html
    assert "No source-summary entries emitted" in html
    assert "Total species" in html and ">0<" in html
    assert "Per-species authority: false" not in html


def test_top_level_authority_stays_distinct_from_species_rows() -> None:
    html = _render(_complete_report())
    species_table = html.split("Per-species source tokens", 1)[1].split("</table>", 1)[0]

    assert "Authoritative for requested vapor pressure" in html
    assert ">false<" in html
    assert "false" not in species_table
    assert "Run-level envelope values are not copied into species rows" in html


def test_non_string_species_tokens_are_malformed_not_verbatim() -> None:
    for malformed, emitted_type in ((7, "number"), (False, "boolean"), (None, "null")):
        report = _complete_report()
        report["species"] = {"Na": malformed}
        html = _render(report)
        species_table = html.split("Per-species source tokens", 1)[1].split(
            "</table>", 1
        )[0]

        assert f"Malformed {emitted_type} source token" in species_table
        assert f'<span class="sec-p10-token">{str(malformed).lower()}</span>' not in species_table


def test_partial_total_species_stays_pending_instead_of_counting_species() -> None:
    report = _complete_report()
    del report["total_species"]
    html = _render(report)
    total_card = html.split("Total species", 1)[1].split("</div></div>", 1)[0]

    assert "Not emitted · pending producer" in total_card
    assert ">2<" not in total_card


def test_partial_summaries_stay_pending_with_derivation_inputs_present() -> None:
    report = _complete_report()
    del report["summary"]
    del report["vapor_pressure_backend_status_summary"]
    html = _render(report)
    summary_grid = html.split('class="sec-p10-summary-grid">', 1)[1].split(
        '<div class="sec-p10-species-block">', 1
    )[0]

    assert "Source summary was not emitted" in summary_grid
    assert "Backend-status summary was not emitted" in summary_grid
    assert ">2<" not in summary_grid
    assert ">50<" not in summary_grid
    assert ">100<" not in summary_grid


def test_missing_envelope_fields_are_not_inferred_from_suggestive_inputs() -> None:
    cases = (
        ("vapor_pressure_backend_status", "Vapor-pressure backend status", "fallback"),
        ("vapor_pressure_backend_status_reason", "Backend-status reason", "vaporock_to_antoine_fallback"),
        ("vapor_pressure_fallback_source", "Vapor-pressure fallback source", "antoine_fallback_from_vaporock"),
        ("authoritative_for_requested_vapor_pressure", "Authoritative for requested vapor pressure", "false"),
    )
    for key, label, forbidden in cases:
        report = _complete_report()
        report["species"] = {"Na": "diagnostic_certified:fallback_authoritative_false"}
        del report[key]
        html = _render(report)
        card = html.split(label, 1)[1].split("</div></div>", 1)[0]

        assert "Not emitted · pending producer" in card
        assert forbidden not in card


def test_source_token_is_escaped_and_never_parsed_into_claims() -> None:
    report = _complete_report()
    report["species"] = {
        "SiO2<script>": 'diagnostic_certified:fallback<&"source"'
    }
    report["total_species"] = 1
    html = _render(report)
    species_table = html.split("Per-species source tokens", 1)[1].split("</table>", 1)[0]

    assert "SiO2&lt;script&gt;" in html
    assert "diagnostic_certified:fallback&lt;&amp;&quot;source&quot;" in html
    assert "<script>" not in html
    assert "Per-species certification" not in html
    assert "Per-species fallback" not in html
    assert "Certified" not in species_table
    assert "Diagnostic" not in species_table
    assert "Fallback" not in species_table
    assert "Source token · verbatim" in html
