from __future__ import annotations

import json
from pathlib import Path
import re
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


def _envelope_card(markup: str, label: str) -> str:
    return markup.split(label, 1)[1].split("</div></div>", 1)[0]


def _summary_table(markup: str, title: str) -> str:
    return markup.split(f"<caption>{title}</caption>", 1)[1].split("</table>", 1)[0]


def _species_table(markup: str) -> str:
    return markup.split("Per-species source tokens", 1)[1].split("</table>", 1)[0]


def _species_block(markup: str) -> str:
    return markup.split('<div class="sec-p10-species-block">', 1)[1].split(
        '<div class="pending sec-p10-pending sec-p10-structured-pending"', 1
    )[0]


def _table_row(table: str, marker: str) -> str:
    tbody = table.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    for fragment in tbody.split("<tr>")[1:]:
        row = f"<tr>{fragment.split('</tr>', 1)[0]}</tr>"
        if marker in row:
            return row
    raise AssertionError(f"row marker not found: {marker}")


def _data_cells(row: str) -> list[str]:
    return re.findall(r'<td(?: class="[^"]+")?>(.*?)</td>', row)


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
    species_table = _species_table(html)
    sodium_row = _table_row(species_table, 'title="Na"')
    silica_row = _table_row(species_table, 'title="SiO2"')
    source_summary = _summary_table(html, "Source summary")
    builtin_row = _table_row(source_summary, ">builtin_authoritative</th>")
    vaporock_row = _table_row(
        source_summary,
        ">vaporock_backsolved_curve_fit:backsolved_vaporock_curve_fit</th>",
    )
    status_summary = _summary_table(html, "Backend-status summary")
    fallback_row = _table_row(status_summary, ">fallback</th>")

    assert "builtin_authoritative" in sodium_row
    assert "vaporock_backsolved_curve_fit:backsolved_vaporock_curve_fit" not in sodium_row
    assert "SiO₂" in silica_row
    assert "vaporock_backsolved_curve_fit:backsolved_vaporock_curve_fit" in silica_row
    assert "--sec-p10-species-color: #e7298a" in sodium_row
    assert "--sec-p10-species-color: #7570b3" in silica_row
    assert ">2<" in _envelope_card(html, "Total species")
    assert ">fallback<" in _envelope_card(html, "Vapor-pressure backend status")
    assert ">vaporock_to_antoine_fallback<" in _envelope_card(html, "Backend-status reason")
    assert ">antoine_fallback_from_vaporock<" in _envelope_card(
        html, "Vapor-pressure fallback source"
    )
    assert ">false<" in _envelope_card(
        html, "Authoritative for requested vapor pressure"
    )
    assert _data_cells(builtin_row) == [
        '<span class="sec-p10-scalar">1</span>',
        '<span class="sec-p10-scalar">50</span>',
    ]
    assert _data_cells(vaporock_row) == [
        '<span class="sec-p10-scalar">1</span>',
        '<span class="sec-p10-scalar">50</span>',
    ]
    assert _data_cells(fallback_row) == [
        '<span class="sec-p10-scalar">2</span>',
        '<span class="sec-p10-scalar">100</span>',
    ]


def test_all_report_states_label_final_equilibrium_not_run_history() -> None:
    for report in (..., None, _complete_report()):
        html = _render(report)

        assert "final-equilibrium source tokens" in html
        assert "run-level" not in html.lower()


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


def test_wrong_typed_fields_are_malformed_while_empty_and_zero_remain_distinct() -> None:
    envelope_cases = (
        ("total_species", "2", "Total species", "string", ">2<"),
        (
            "vapor_pressure_backend_status",
            False,
            "Vapor-pressure backend status",
            "boolean",
            ">false<",
        ),
        (
            "authoritative_for_requested_vapor_pressure",
            "false",
            "Authoritative for requested vapor pressure",
            "string",
            ">false<",
        ),
    )
    for key, malformed, label, emitted_type, clean_claim in envelope_cases:
        report = _complete_report()
        report[key] = malformed
        card = _envelope_card(_render(report), label)

        assert f"Malformed {emitted_type} (emitted)" in card
        assert clean_claim not in card

    report = _complete_report()
    report["summary"] = {
        "builtin_authoritative": {"count": "1", "percentage": "100"}
    }
    report["vapor_pressure_backend_status_summary"] = {"fallback": None}
    html = _render(report)
    source_row = _table_row(
        _summary_table(html, "Source summary"), ">builtin_authoritative</th>"
    )
    status_row = _table_row(
        _summary_table(html, "Backend-status summary"), ">fallback</th>"
    )
    assert _data_cells(source_row) == [
        '<span class="sec-p10-malformed">Malformed string (emitted)</span>',
        '<span class="sec-p10-malformed">Malformed string (emitted)</span>',
    ]
    assert _data_cells(status_row) == [
        '<span class="sec-p10-malformed">Malformed null (emitted)</span>',
        '<span class="sec-p10-malformed">Malformed null (emitted)</span>',
    ]

    empty_report = _complete_report()
    empty_report.update(
        {
            "species": {},
            "summary": {},
            "total_species": 0,
            "vapor_pressure_backend_status": "",
            "vapor_pressure_backend_status_summary": {},
            "vapor_pressure_backend_status_reason": "",
            "vapor_pressure_fallback_source": "",
            "authoritative_for_requested_vapor_pressure": None,
        }
    )
    empty_html = _render(empty_report)
    assert ">0<" in _envelope_card(empty_html, "Total species")
    assert "empty string (emitted)" in _envelope_card(
        empty_html, "Vapor-pressure backend status"
    )
    assert "null (emitted)" in _envelope_card(
        empty_html, "Authoritative for requested vapor pressure"
    )


def test_empty_species_is_emitted_empty_not_default_authority() -> None:
    report = _complete_report()
    report["species"] = {}
    report["summary"] = {}
    report["total_species"] = 0
    report["vapor_pressure_backend_status"] = ""
    report["vapor_pressure_backend_status_summary"] = {}
    report["vapor_pressure_backend_status_reason"] = ""
    report["vapor_pressure_fallback_source"] = ""
    report["authoritative_for_requested_vapor_pressure"] = None
    html = _render(report)
    species_block = _species_block(html)
    summary_grid = html.split('class="sec-p10-summary-grid">', 1)[1].split(
        '<div class="sec-p10-species-block">', 1
    )[0]

    assert '<p class="sec-p10-empty">No per-species sources emitted.</p>' in species_block
    assert "<table" not in species_block
    assert ">0<" in _envelope_card(html, "Total species")
    assert "No source-summary entries emitted" in summary_grid
    assert "No backend-status summary entries emitted" in summary_grid
    assert "false" not in species_block.lower()
    for unsupported_claim in (
        "authority",
        "authoritative",
        "status",
        "reference",
        "diagnostic",
        "certification",
        "certified",
        "fallback",
        "extrapolation",
        "uncertainty",
    ):
        assert unsupported_claim not in species_block.lower()


def test_top_level_authority_stays_distinct_from_species_rows() -> None:
    for emitted in (False, True):
        report = _complete_report()
        report["authoritative_for_requested_vapor_pressure"] = emitted
        html = _render(report)
        authority_card = _envelope_card(
            html, "Authoritative for requested vapor pressure"
        )
        species_table = _species_table(html)
        rendered = str(emitted).lower()

        assert f">{rendered}<" in authority_card
        assert f">{rendered}<" not in species_table
        assert "Final-equilibrium envelope values are not copied into species rows" in html


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


def test_present_report_with_missing_species_stays_pending_not_emitted_empty() -> None:
    report = _complete_report()
    del report["species"]
    html = _render(report)
    species_block = _species_block(html)

    assert "Per-species source tokens were not emitted" in species_block
    assert "No per-species sources emitted" not in species_block
    assert "<table" not in species_block
    assert "builtin_authoritative" not in species_block
    assert ">2<" not in species_block


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


def test_partial_summary_cells_stay_pending_instead_of_deriving_sibling_values() -> None:
    cases = (
        ("summary", "Source summary", "builtin_authoritative", "percentage", 50),
        ("summary", "Source summary", "builtin_authoritative", "count", 1),
        (
            "vapor_pressure_backend_status_summary",
            "Backend-status summary",
            "fallback",
            "percentage",
            100,
        ),
        (
            "vapor_pressure_backend_status_summary",
            "Backend-status summary",
            "fallback",
            "count",
            2,
        ),
    )
    for summary_key, title, token, missing_key, derivable in cases:
        report = _complete_report()
        del report[summary_key][token][missing_key]
        html = _render(report)
        row = _table_row(_summary_table(html, title), f">{token}</th>")
        count_cell, percentage_cell = _data_cells(row)
        missing_cell = count_cell if missing_key == "count" else percentage_cell

        assert "Not emitted · pending producer" in missing_cell
        assert f">{derivable}<" not in missing_cell


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
    report["summary"] = {
        "<script>summary()</script>": {"count": 1, "percentage": 100.0}
    }
    report["vapor_pressure_backend_status_summary"] = {
        "<b>fallback</b>": {"count": 1, "percentage": 100.0}
    }
    report["vapor_pressure_backend_status_reason"] = '<img src=x onerror="alert(1)">'
    report["total_species"] = 1
    html = _render(report)
    species_table = _species_table(html)
    species_row = _table_row(species_table, "SiO2&lt;script&gt;")
    reason_card = _envelope_card(html, "Backend-status reason")
    source_summary = _summary_table(html, "Source summary")
    status_summary = _summary_table(html, "Backend-status summary")

    assert _data_cells(species_row) == [
        '<span class="sec-p10-token">diagnostic_certified:fallback&lt;&amp;&quot;source&quot;</span>'
    ]
    assert species_row.count("<td>") == 1
    assert species_row.count("SiO2&lt;script&gt;") == 2
    assert "<script>" not in species_row
    assert "&amp;lt;" not in species_row
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in reason_card
    assert "<img" not in reason_card
    assert "&lt;script&gt;summary()&lt;/script&gt;" in source_summary
    assert "<script>" not in source_summary
    assert "&lt;b&gt;fallback&lt;/b&gt;" in status_summary
    assert "<b>" not in status_summary
    assert "<span>certified</span>" not in species_row.lower()
    assert "<span>fallback</span>" not in species_row.lower()
