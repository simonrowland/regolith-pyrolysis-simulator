import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "web" / "report_viewer"
LABELS_PATH = ROOT / "labels.js"
PANEL_PATH = ROOT / "panels" / "p7-energy.js"
DERIVE_ROWS = object()
UNDEFINED_ROWS = object()

METRIC_HEADINGS = (
    "Cumulative electrical load",
    "Cumulative diagnostic evaporation-enthalpy estimate",
    "Electrical energy",
    "Diagnostic evaporation-enthalpy estimate",
    "Latent vaporization component",
    "Reaction / dissociation component",
    "Terminal-timestep scoped combined energy",
    "Cumulative scoped combined energy",
)

CUMULATIVE_ROWS = (
    "Electrical load",
    "Diagnostic evaporation-enthalpy estimate",
    "Latent vaporization component",
    "Reaction / dissociation component",
    "Scoped electrical + evaporation energy",
)

EVAPORATION_ROWS = (
    "Diagnostic evaporation-enthalpy sink estimate",
    "Reaction / dissociation enthalpy sink",
    "Product-vapor enthalpy sink",
    "Net unallocated",
)

DIAGNOSTIC_ROW_AUTHORITY = "Diagnostic · Ledger-neutral estimate"
COMBINED_ROW_AUTHORITY = "Contains diagnostic evaporation-enthalpy estimate"
CUMULATIVE_ROW_AUTHORITIES = {
    "Diagnostic evaporation-enthalpy estimate": DIAGNOSTIC_ROW_AUTHORITY,
    "Latent vaporization component": DIAGNOSTIC_ROW_AUTHORITY,
    "Reaction / dissociation component": DIAGNOSTIC_ROW_AUTHORITY,
    "Scoped electrical + evaporation energy": COMBINED_ROW_AUTHORITY,
}


def _artifact(*summaries: dict) -> dict:
    return {
        "timesteps": [{"summary": summary} for summary in summaries],
    }


def _render_panel(artifact: dict, rows=DERIVE_ROWS) -> str:
    payload = {"artifact": artifact, "rows_mode": "derive"}
    if rows is UNDEFINED_ROWS:
        payload["rows_mode"] = "undefined"
    elif rows is not DERIVE_ROWS:
        payload["rows_mode"] = "explicit"
        payload["rows"] = rows

    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
function reviveSpecial(value) {
  if (value === "__P7_NAN__") return Number.NaN;
  if (Array.isArray(value)) return value.map(reviveSpecial);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, reviveSpecial(item)]));
  }
  return value;
}
const payload = reviveSpecial(JSON.parse(process.argv[4]));
const artifact = payload.artifact;
const rows = payload.rows_mode === "derive"
  ? artifact.timesteps.map((timestep) => timestep.summary)
  : (payload.rows_mode === "undefined" ? undefined : payload.rows);
const panel = context.ReportPanels.find((candidate) => candidate.id === "sec-p7-energy");
process.stdout.write(panel.render(artifact, rows, [], {}));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS_PATH), str(PANEL_PATH), json.dumps(payload)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _articles(html: str) -> list[str]:
    return re.findall(r"<article\b.*?</article>", html)


def _article(html: str, heading: str) -> str:
    matches = [article for article in _articles(html) if f"<h3>{heading}</h3>" in article]
    assert len(matches) == 1, f"expected one article headed {heading!r}, got {len(matches)}"
    return matches[0]


def _row(html: str, label: str) -> str:
    marker = f'<th scope="row">{label}</th>'
    marker_at = html.index(marker)
    start = html.rfind("<tr>", 0, marker_at)
    end = html.index("</tr>", marker_at) + len("</tr>")
    return html[start:end]


def _expected_row(label: str, value: str, authority: str | None = None) -> str:
    badge = (
        f'<span class="sec-p7-energy-row-authority">{authority}</span>'
        if authority
        else ""
    )
    return f'<tr><th scope="row">{label}</th><td>{value}{badge}</td></tr>'


def _missing(state: str = "not emitted") -> str:
    return f'<span class="sec-p7-energy-missing">pending · {state}</span>'


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
        '<h3>Cumulative diagnostic evaporation-enthalpy estimate</h3>'
        '<div class="sec-p7-energy-value">43.25 kWh</div>'
        '<p class="sec-p7-energy-basis">Cumulative through the terminal timestep</p>'
    ) in html
    assert (
        '<h3>Electrical energy</h3>'
        '<div class="sec-p7-energy-value">2.75 kWh</div>'
        '<p class="sec-p7-energy-basis">Terminal timestep · one-hour interval</p>'
    ) in html
    assert (
        '<h3>Diagnostic evaporation-enthalpy estimate</h3>'
        '<div class="sec-p7-energy-value">7.25 kWh</div>'
        '<p class="sec-p7-energy-basis">Terminal timestep · one-hour interval</p>'
    ) in html
    assert (
        '<h3>Latent vaporization component</h3>'
        '<div class="sec-p7-energy-value">1.25 kWh</div>'
        '<p class="sec-p7-energy-basis">Terminal timestep · one-hour interval</p>'
    ) in html
    assert (
        '<h3>Reaction / dissociation component</h3>'
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
    cumulative_rows = {
        "Electrical load": "80.25 kWh",
        "Diagnostic evaporation-enthalpy estimate": "43.25 kWh",
        "Latent vaporization component": "12.5 kWh",
        "Reaction / dissociation component": "30.75 kWh",
        "Scoped electrical + evaporation energy": "123.5 kWh",
    }
    evaporation_rows = {
        "Diagnostic evaporation-enthalpy sink estimate": "7.25 kWh",
        "Reaction / dissociation enthalpy sink": "6 kWh",
        "Product-vapor enthalpy sink": "1.25 kWh",
        "Net unallocated": "0 kWh",
    }
    cumulative = _article(html, "Cumulative emitted component breakdown")
    evaporation = _article(html, "Terminal-timestep diagnostic evaporation breakdown")
    for label, value in cumulative_rows.items():
        assert _row(cumulative, label) == _expected_row(
            label, value, CUMULATIVE_ROW_AUTHORITIES.get(label)
        )
    for label, value in evaporation_rows.items():
        assert _row(evaporation, label) == _expected_row(label, value)

    for article in _articles(html):
        assert article.count("<b>Scope</b> · Electrical + known evaporation enthalpy") == 1
        assert article.count("<b>Furnace heat</b> · Partial") == 1
    assert "custom_heat_sink" not in html
    assert "not viewer-summed" in html


def test_dynamic_breakdown_keys_render_their_emitted_values():
    html = _render_panel(_artifact(_complete_summary()))
    cumulative = _article(html, "Cumulative emitted component breakdown")
    evaporation = _article(html, "Terminal-timestep diagnostic evaporation breakdown")

    assert _row(cumulative, "Custom heat sink") == _expected_row(
        "Custom heat sink", "4.125 kWh"
    )
    assert _row(evaporation, "Custom trace") == _expected_row(
        "Custom trace", "0.125 kWh"
    )


def test_absent_fields_render_pending_without_zero_fallback():
    html = _render_panel(_artifact({}))

    for heading in METRIC_HEADINGS:
        assert _missing() in _article(html, heading)
    for label in CUMULATIVE_ROWS + EVAPORATION_ROWS:
        assert _row(html, label) == f'<tr><th scope="row">{label}</th><td>{_missing()}</td></tr>'
    for heading in (
        "Cumulative emitted component breakdown",
        "Terminal-timestep diagnostic evaporation breakdown",
    ):
        article = _article(html, heading)
        assert "Breakdown not emitted; no components are inferred." in article
    for article in _articles(html):
        assert article.count("<b>Scope</b> · Not emitted") == 1
        assert article.count("<b>Furnace heat</b> · Not emitted") == 1
    assert ">0 kWh<" not in html

    one_breakdown_absent = _render_panel(_artifact({
        "energy_cumulative_breakdown_kWh": {"electrical": 4.0},
    }))
    cumulative = _article(one_breakdown_absent, "Cumulative emitted component breakdown")
    evaporation = _article(
        one_breakdown_absent, "Terminal-timestep diagnostic evaporation breakdown"
    )
    assert "Breakdown not emitted; no components are inferred." not in cumulative
    assert "Breakdown not emitted; no components are inferred." in evaporation


def test_partial_inputs_do_not_manufacture_derived_energy():
    hourly_html = _render_panel(_artifact({
        "energy_electrical_kWh": 13.0,
        "energy_latent_kWh": 3.0,
        "energy_dissociation_kWh": 8.0,
    }))
    cumulative_thermal_html = _render_panel(_artifact({
        "energy_cumulative_breakdown_kWh": {
            "latent": 2.0,
            "dissociation": 5.0,
        },
    }))
    cumulative_combined_html = _render_panel(_artifact({
        "energy_cumulative_breakdown_kWh": {
            "electrical": 70.0,
            "evaporation_thermal": 20.0,
        },
    }))
    evaporation_html = _render_panel(_artifact({
        "energy_evaporation_breakdown_kWh": {
            "reaction_disproportionation_enthalpy_sink": 6.0,
            "product_vapor_enthalpy_sink": 1.25,
        },
    }))
    duplicate_html = _render_panel(_artifact({
        "energy_cumulative_breakdown_kWh": {
            "latent": 2.0,
            "electrical_plus_evaporation": 91.0,
        },
        "energy_evaporation_breakdown_kWh": {
            "evaporation_enthalpy_sink": 7.25,
            "reaction_disproportionation_enthalpy_sink": 6.0,
            "product_vapor_enthalpy_sink": 1.25,
        },
    }))
    multi_hour_html = _render_panel(_artifact(
        {
            "energy_electrical_kWh": 13.0,
            "energy_evaporation_thermal_kWh": 4.0,
            "energy_latent_kWh": 1.0,
            "energy_dissociation_kWh": 3.0,
            "energy_electrical_plus_evaporation_kWh": 17.0,
        },
        {
            "energy_electrical_kWh": 17.0,
            "energy_evaporation_thermal_kWh": 7.0,
            "energy_latent_kWh": 2.0,
            "energy_dissociation_kWh": 5.0,
            "energy_electrical_plus_evaporation_kWh": 24.0,
        },
    ))

    hourly_thermal = _article(hourly_html, "Diagnostic evaporation-enthalpy estimate")
    hourly_combined = _article(hourly_html, "Terminal-timestep scoped combined energy")
    assert _missing() in hourly_thermal and "11 kWh" not in hourly_thermal
    assert _missing() in hourly_combined and "24 kWh" not in hourly_combined

    cumulative_thermal = _article(
        cumulative_thermal_html, "Cumulative diagnostic evaporation-enthalpy estimate"
    )
    assert _missing() in cumulative_thermal and "7 kWh" not in cumulative_thermal
    cumulative_thermal_row = _row(
        cumulative_thermal_html, "Diagnostic evaporation-enthalpy estimate"
    )
    assert _missing() in cumulative_thermal_row and "7 kWh" not in cumulative_thermal_row
    # Hourly cards must not fall back to cumulative component values (F5).
    for heading, forbidden in (
        ("Latent vaporization component", "2 kWh"),
        ("Reaction / dissociation component", "5 kWh"),
    ):
        article = _article(cumulative_thermal_html, heading)
        assert _missing() in article
        assert forbidden not in article
    assert (
        _row(cumulative_thermal_html, "Latent vaporization component")
        == _expected_row("Latent vaporization component", "2 kWh", DIAGNOSTIC_ROW_AUTHORITY)
    )
    assert (
        _row(cumulative_thermal_html, "Reaction / dissociation component")
        == _expected_row("Reaction / dissociation component", "5 kWh", DIAGNOSTIC_ROW_AUTHORITY)
    )

    cumulative_combined = _article(
        cumulative_combined_html, "Cumulative scoped combined energy"
    )
    assert _missing() in cumulative_combined and "90 kWh" not in cumulative_combined
    cumulative_combined_row = _row(
        cumulative_combined_html, "Scoped electrical + evaporation energy"
    )
    assert _missing() in cumulative_combined_row and "90 kWh" not in cumulative_combined_row
    for heading, forbidden_value in (
        ("Electrical energy", "70 kWh"),
        ("Diagnostic evaporation-enthalpy estimate", "20 kWh"),
    ):
        article = _article(cumulative_combined_html, heading)
        assert _missing() in article
        assert forbidden_value not in article

    evaporation_total = _row(
        evaporation_html, "Diagnostic evaporation-enthalpy sink estimate"
    )
    assert _missing() in evaporation_total and "7.25 kWh" not in evaporation_total

    duplicate_targets = {
        "Latent vaporization component": "2 kWh",
        "Reaction / dissociation component": "6 kWh",
        "Diagnostic evaporation-enthalpy estimate": "7.25 kWh",
        "Terminal-timestep scoped combined energy": "91 kWh",
        "Cumulative scoped combined energy": "91 kWh",
    }
    for heading, forbidden_value in duplicate_targets.items():
        article = _article(duplicate_html, heading)
        assert _missing() in article
        assert forbidden_value not in article

    cumulative_sum_targets = {
        "Cumulative electrical load": "30 kWh",
        "Cumulative diagnostic evaporation-enthalpy estimate": "11 kWh",
        "Cumulative scoped combined energy": "41 kWh",
    }
    for heading, forbidden_value in cumulative_sum_targets.items():
        article = _article(multi_hour_html, heading)
        assert _missing() in article
        assert forbidden_value not in article
    cumulative_sum_rows = {
        "Electrical load": "30 kWh",
        "Diagnostic evaporation-enthalpy estimate": "11 kWh",
        "Latent vaporization component": "3 kWh",
        "Reaction / dissociation component": "8 kWh",
        "Scoped electrical + evaporation energy": "41 kWh",
    }
    for label, forbidden_value in cumulative_sum_rows.items():
        row = _row(multi_hour_html, label)
        assert _missing() in row
        assert forbidden_value not in row


def test_hourly_combined_is_direct_emitted_value_not_viewer_sum():
    absent = _render_panel(_artifact({
        "energy_electrical_kWh": 10.0,
        "energy_evaporation_thermal_kWh": 3.0,
    }))
    mismatch = _render_panel(_artifact({
        "energy_electrical_kWh": 10.0,
        "energy_evaporation_thermal_kWh": 3.0,
        "energy_electrical_plus_evaporation_kWh": 99.0,
    }))

    absent_combined = _article(absent, "Terminal-timestep scoped combined energy")
    assert _missing() in absent_combined
    assert ">13 kWh<" not in absent_combined

    emitted_combined = _article(mismatch, "Terminal-timestep scoped combined energy")
    assert ">99 kWh<" in emitted_combined
    assert ">13 kWh<" not in emitted_combined


def test_partial_path_matrix_keeps_each_derivable_target_pending():
    cases = (
        (
            "hourly electrical from combined minus thermal",
            {
                "energy_evaporation_thermal_kWh": 11.0,
                "energy_electrical_plus_evaporation_kWh": 37.0,
            },
            (("article", "Electrical energy", "26 kWh"),),
        ),
        (
            "hourly thermal from combined minus electrical",
            {
                "energy_electrical_kWh": 26.0,
                "energy_electrical_plus_evaporation_kWh": 37.0,
            },
            (("article", "Diagnostic evaporation-enthalpy estimate", "11 kWh"),),
        ),
        (
            "hourly latent from thermal minus reaction",
            {
                "energy_evaporation_thermal_kWh": 11.0,
                "energy_dissociation_kWh": 7.0,
            },
            (("article", "Latent vaporization component", "4 kWh"),),
        ),
        (
            "hourly reaction from thermal minus latent",
            {
                "energy_evaporation_thermal_kWh": 11.0,
                "energy_latent_kWh": 4.0,
            },
            (("article", "Reaction / dissociation component", "7 kWh"),),
        ),
        (
            "cumulative electrical from combined minus thermal",
            {
                "energy_cumulative_breakdown_kWh": {
                    "evaporation_thermal": 11.0,
                    "electrical_plus_evaporation": 37.0,
                },
            },
            (
                ("article", "Cumulative electrical load", "26 kWh"),
                ("row", "Electrical load", "26 kWh"),
            ),
        ),
        (
            "cumulative thermal from combined minus electrical",
            {
                "energy_cumulative_breakdown_kWh": {
                    "electrical": 26.0,
                    "electrical_plus_evaporation": 37.0,
                },
            },
            (
                ("article", "Cumulative diagnostic evaporation-enthalpy estimate", "11 kWh"),
                ("row", "Diagnostic evaporation-enthalpy estimate", "11 kWh"),
            ),
        ),
        (
            "cumulative latent from thermal minus reaction",
            {
                "energy_cumulative_breakdown_kWh": {
                    "evaporation_thermal": 11.0,
                    "dissociation": 7.0,
                },
            },
            (("row", "Latent vaporization component", "4 kWh"),),
        ),
        (
            "cumulative reaction from thermal minus latent",
            {
                "energy_cumulative_breakdown_kWh": {
                    "evaporation_thermal": 11.0,
                    "latent": 4.0,
                },
            },
            (("row", "Reaction / dissociation component", "7 kWh"),),
        ),
        (
            "cumulative combined from electrical plus thermal",
            {
                "energy_cumulative_breakdown_kWh": {
                    "electrical": 26.0,
                    "evaporation_thermal": 11.0,
                },
            },
            (("row", "Scoped electrical + evaporation energy", "37 kWh"),),
        ),
        (
            "cumulative headline from breakdown combined",
            {
                "energy_cumulative_breakdown_kWh": {
                    "electrical_plus_evaporation": 91.0,
                },
            },
            (("article", "Cumulative scoped combined energy", "91 kWh"),),
        ),
        (
            "cumulative breakdown combined from headline",
            {
                "energy_electrical_plus_evaporation_cumulative_kWh": 91.0,
            },
            (("row", "Scoped electrical + evaporation energy", "91 kWh"),),
        ),
        (
            "evaporation total from reaction plus product",
            {
                "energy_evaporation_breakdown_kWh": {
                    "reaction_disproportionation_enthalpy_sink": 7.0,
                    "product_vapor_enthalpy_sink": 4.0,
                },
            },
            (("row", "Diagnostic evaporation-enthalpy sink estimate", "11 kWh"),),
        ),
        (
            "evaporation reaction from total minus product",
            {
                "energy_evaporation_breakdown_kWh": {
                    "evaporation_enthalpy_sink": 11.0,
                    "product_vapor_enthalpy_sink": 4.0,
                },
            },
            (("row", "Reaction / dissociation enthalpy sink", "7 kWh"),),
        ),
        (
            "evaporation product from total minus reaction",
            {
                "energy_evaporation_breakdown_kWh": {
                    "evaporation_enthalpy_sink": 11.0,
                    "reaction_disproportionation_enthalpy_sink": 7.0,
                },
            },
            (("row", "Product-vapor enthalpy sink", "4 kWh"),),
        ),
        (
            "evaporation net from total minus allocated components",
            {
                "energy_evaporation_breakdown_kWh": {
                    "evaporation_enthalpy_sink": 11.0,
                    "reaction_disproportionation_enthalpy_sink": 7.0,
                    "product_vapor_enthalpy_sink": 4.0,
                },
            },
            (("row", "Net unallocated", "0 kWh"),),
        ),
        (
            "hourly fields from cumulative breakdown",
            {
                "energy_cumulative_breakdown_kWh": {
                    "electrical": 26.0,
                    "evaporation_thermal": 11.0,
                    "latent": 4.0,
                    "dissociation": 7.0,
                    "electrical_plus_evaporation": 37.0,
                },
            },
            (
                ("article", "Electrical energy", "26 kWh"),
                ("article", "Diagnostic evaporation-enthalpy estimate", "11 kWh"),
                ("article", "Latent vaporization component", "4 kWh"),
                ("article", "Reaction / dissociation component", "7 kWh"),
                ("article", "Terminal-timestep scoped combined energy", "37 kWh"),
            ),
        ),
        (
            "hourly fields from evaporation breakdown",
            {
                "energy_evaporation_breakdown_kWh": {
                    "evaporation_enthalpy_sink": 11.0,
                    "reaction_disproportionation_enthalpy_sink": 7.0,
                    "product_vapor_enthalpy_sink": 4.0,
                },
            },
            (
                ("article", "Diagnostic evaporation-enthalpy estimate", "11 kWh"),
                ("article", "Latent vaporization component", "4 kWh"),
                ("article", "Reaction / dissociation component", "7 kWh"),
            ),
        ),
        (
            "cumulative fields from hourly values",
            {
                "energy_electrical_kWh": 26.0,
                "energy_evaporation_thermal_kWh": 11.0,
                "energy_latent_kWh": 4.0,
                "energy_dissociation_kWh": 7.0,
                "energy_electrical_plus_evaporation_kWh": 37.0,
            },
            (
                ("article", "Cumulative electrical load", "26 kWh"),
                ("article", "Cumulative diagnostic evaporation-enthalpy estimate", "11 kWh"),
                ("article", "Cumulative scoped combined energy", "37 kWh"),
                ("row", "Electrical load", "26 kWh"),
                ("row", "Diagnostic evaporation-enthalpy estimate", "11 kWh"),
                ("row", "Latent vaporization component", "4 kWh"),
                ("row", "Reaction / dissociation component", "7 kWh"),
                ("row", "Scoped electrical + evaporation energy", "37 kWh"),
            ),
        ),
        (
            "evaporation breakdown from hourly values",
            {
                "energy_evaporation_thermal_kWh": 11.0,
                "energy_latent_kWh": 4.0,
                "energy_dissociation_kWh": 7.0,
            },
            (
                ("row", "Diagnostic evaporation-enthalpy sink estimate", "11 kWh"),
                ("row", "Reaction / dissociation enthalpy sink", "7 kWh"),
                ("row", "Product-vapor enthalpy sink", "4 kWh"),
                ("row", "Net unallocated", "0 kWh"),
            ),
        ),
    )

    for case_name, summary, checks in cases:
        html = _render_panel(_artifact(summary))
        for region_kind, target, forbidden_value in checks:
            region = _article(html, target) if region_kind == "article" else _row(html, target)
            assert _missing() in region, case_name
            assert f">{forbidden_value}<" not in region, case_name


def test_cumulative_deltas_do_not_become_terminal_hourly_values():
    html = _render_panel(_artifact(
        {
            "energy_electrical_plus_evaporation_cumulative_kWh": 14.0,
            "energy_cumulative_breakdown_kWh": {
                "electrical": 10.0,
                "evaporation_thermal": 4.0,
                "latent": 1.0,
                "dissociation": 3.0,
                "electrical_plus_evaporation": 14.0,
            },
        },
        {
            "energy_electrical_plus_evaporation_cumulative_kWh": 37.0,
            "energy_cumulative_breakdown_kWh": {
                "electrical": 26.0,
                "evaporation_thermal": 11.0,
                "latent": 4.0,
                "dissociation": 7.0,
                "electrical_plus_evaporation": 37.0,
            },
        },
    ))

    hourly_deltas = {
        "Electrical energy": "16 kWh",
        "Diagnostic evaporation-enthalpy estimate": "7 kWh",
        "Latent vaporization component": "3 kWh",
        "Reaction / dissociation component": "4 kWh",
        "Terminal-timestep scoped combined energy": "23 kWh",
    }
    for heading, forbidden_value in hourly_deltas.items():
        article = _article(html, heading)
        assert _missing() in article
        assert f">{forbidden_value}<" not in article

    evaporation_deltas = {
        "Diagnostic evaporation-enthalpy sink estimate": "7 kWh",
        "Reaction / dissociation enthalpy sink": "4 kWh",
        "Product-vapor enthalpy sink": "3 kWh",
        "Net unallocated": "0 kWh",
    }
    for label, forbidden_value in evaporation_deltas.items():
        row = _row(html, label)
        assert _missing() in row
        assert f">{forbidden_value}<" not in row


def test_sparse_breakdowns_keep_missing_components_pending():
    html = _render_panel(_artifact({
        "energy_cumulative_breakdown_kWh": {"electrical": 50.0},
        "energy_evaporation_breakdown_kWh": {
            "evaporation_enthalpy_sink": 5.0,
        },
        "energy_scope": "electrical_plus_known_evaporation_enthalpy",
        "furnace_heat_status": "partial",
    }))

    assert _row(html, "Electrical load") == (
        '<tr><th scope="row">Electrical load</th><td>50 kWh</td></tr>'
    )
    assert _row(html, "Diagnostic evaporation-enthalpy sink estimate") == (
        '<tr><th scope="row">Diagnostic evaporation-enthalpy sink estimate</th>'
        '<td>5 kWh</td></tr>'
    )
    for label in CUMULATIVE_ROWS[1:] + EVAPORATION_ROWS[1:]:
        assert _row(html, label) == f'<tr><th scope="row">{label}</th><td>{_missing()}</td></tr>'
    assert ">0 kWh<" not in html


def test_electrical_only_scope_and_heat_status_repeat_on_every_card():
    html = _render_panel(_artifact(_complete_summary(
        energy_scope="electrical_only",
        furnace_heat_status="not_tracked",
    )))

    articles = _articles(html)
    assert len(articles) == 10
    for article in articles:
        assert article.count("<b>Scope</b> · Electrical only") == 1
        assert article.count("<b>Furnace heat</b> · Not tracked") == 1
        assert article.count('aria-label="Energy scope and furnace heat coverage"') == 1


def test_diagnostic_authority_is_bound_to_affected_emitted_values():
    html = _render_panel(_artifact(_complete_summary()))
    diagnostic_headings = METRIC_HEADINGS[1:2] + METRIC_HEADINGS[3:] + (
        "Terminal-timestep diagnostic evaporation breakdown",
    )

    for heading in diagnostic_headings:
        article = _article(html, heading)
        assert article.count("<b>Diagnostic</b> · Ledger-neutral estimate") == 1
    for heading in ("Cumulative electrical load", "Electrical energy"):
        assert "<b>Diagnostic</b>" not in _article(html, heading)

    cumulative = _article(html, "Cumulative emitted component breakdown")
    assert "<b>Diagnostic</b>" not in cumulative
    assert _row(cumulative, "Electrical load") == _expected_row(
        "Electrical load", "80.25 kWh"
    )
    for label, value in (
        ("Diagnostic evaporation-enthalpy estimate", "43.25 kWh"),
        ("Latent vaporization component", "12.5 kWh"),
        ("Reaction / dissociation component", "30.75 kWh"),
    ):
        assert _row(cumulative, label) == _expected_row(
            label, value, DIAGNOSTIC_ROW_AUTHORITY
        )
    assert _row(cumulative, "Scoped electrical + evaporation energy") == _expected_row(
        "Scoped electrical + evaporation energy", "123.5 kWh", COMBINED_ROW_AUTHORITY
    )

    missing_flags = _render_panel(_artifact({
        "energy_evaporation_thermal_kWh": 7.25,
    }))
    diagnostic = _article(missing_flags, "Diagnostic evaporation-enthalpy estimate")
    assert diagnostic.count("<b>Diagnostic</b> · Ledger-neutral estimate") == 1
    assert diagnostic.count("<b>Scope</b> · Not emitted") == 1
    assert diagnostic.count("<b>Furnace heat</b> · Not emitted") == 1


def test_zero_breakdown_values_keep_row_authority_and_dynamic_values():
    html = _render_panel(_artifact(_complete_summary(
        energy_cumulative_breakdown_kWh={
            "electrical": 0.0,
            "evaporation_thermal": 0.0,
            "latent": 0.0,
            "dissociation": 0.0,
            "electrical_plus_evaporation": 0.0,
            "custom_zero": 0.0,
        },
        energy_evaporation_breakdown_kWh={"custom_zero_trace": 0.0},
    )))
    cumulative = _article(html, "Cumulative emitted component breakdown")
    evaporation = _article(html, "Terminal-timestep diagnostic evaporation breakdown")

    assert _row(cumulative, "Electrical load") == _expected_row(
        "Electrical load", "0 kWh"
    )
    for label in (
        "Diagnostic evaporation-enthalpy estimate",
        "Latent vaporization component",
        "Reaction / dissociation component",
    ):
        assert _row(cumulative, label) == _expected_row(
            label, "0 kWh", DIAGNOSTIC_ROW_AUTHORITY
        )
    assert _row(cumulative, "Scoped electrical + evaporation energy") == _expected_row(
        "Scoped electrical + evaporation energy", "0 kWh", COMBINED_ROW_AUTHORITY
    )
    assert _row(cumulative, "Custom zero") == _expected_row("Custom zero", "0 kWh")
    assert _row(evaporation, "Custom zero trace") == _expected_row(
        "Custom zero trace", "0 kWh"
    )


def test_cro2_oxidation_uses_mixed_reaction_dissociation_label():
    html = _render_panel(_artifact(_complete_summary(
        energy_dissociation_kWh=1.63548484218,
        energy_cumulative_breakdown_kWh={"dissociation": 3.27096968436},
        energy_evaporation_breakdown_kWh={
            "reaction_disproportionation_enthalpy_sink": 1.63548484218,
        },
    )))

    hourly = _article(html, "Reaction / dissociation component")
    assert '<div class="sec-p7-energy-value">1.635 kWh</div>' in hourly
    assert _row(html, "Reaction / dissociation component") == (
        _expected_row(
            "Reaction / dissociation component", "3.271 kWh", DIAGNOSTIC_ROW_AUTHORITY
        )
    )
    assert _row(html, "Reaction / dissociation enthalpy sink") == (
        '<tr><th scope="row">Reaction / dissociation enthalpy sink</th>'
        '<td>1.635 kWh</td></tr>'
    )
    assert "<h3>Dissociation component</h3>" not in html
    assert "Reaction / disproportionation enthalpy sink" not in html


def test_terminal_row_is_the_only_summary_source():
    html = _render_panel(_artifact(
        _complete_summary(
            energy_electrical_kWh=13.0,
            energy_electrical_plus_evaporation_cumulative_kWh=10.0,
            energy_cumulative_breakdown_kWh={"electrical": 9.0},
        ),
        _complete_summary(
            energy_electrical_kWh=17.0,
            energy_electrical_plus_evaporation_cumulative_kWh=123.5,
            energy_cumulative_breakdown_kWh={"electrical": 80.25},
        ),
    ))

    electrical = _article(html, "Electrical energy")
    cumulative_electrical = _article(html, "Cumulative electrical load")
    cumulative_combined = _article(html, "Cumulative scoped combined energy")
    assert ">17 kWh<" in electrical and ">13 kWh<" not in electrical
    assert ">80.25 kWh<" in cumulative_electrical and ">9 kWh<" not in cumulative_electrical
    assert ">123.5 kWh<" in cumulative_combined and ">10 kWh<" not in cumulative_combined


def test_direct_render_distinguishes_absent_empty_malformed_and_zero():
    absent = _render_panel({}, rows=UNDEFINED_ROWS)
    null_rows = _render_panel({}, rows=None)
    empty_rows = _render_panel({}, rows=[])
    malformed_rows = _render_panel({}, rows={"summary": {}})
    empty_summary = _render_panel({}, rows=[None])
    malformed_summary = _render_panel({}, rows=[42])
    values = _render_panel({}, rows=[{
        "energy_electrical_kWh": None,
        "energy_evaporation_thermal_kWh": "",
        "energy_latent_kWh": "not-a-number",
        "energy_dissociation_kWh": 0,
        "energy_electrical_plus_evaporation_kWh": "__P7_NAN__",
        "energy_cumulative_breakdown_kWh": {},
        "energy_evaporation_breakdown_kWh": [],
        "energy_scope": "",
        "furnace_heat_status": {},
    }])

    assert "No terminal timestep summary was emitted." in absent
    assert "Terminal timestep rows were empty." in null_rows
    assert "Terminal timestep rows were empty." in empty_rows
    assert "Terminal timestep rows were malformed." in malformed_rows
    assert "Terminal timestep summary was empty." in empty_summary
    assert "Terminal timestep summary was malformed." in malformed_summary
    assert _missing("emitted empty") in _article(values, "Electrical energy")
    assert _missing("emitted empty") in _article(
        values, "Diagnostic evaporation-enthalpy estimate"
    )
    assert _missing("malformed") in _article(values, "Latent vaporization component")
    assert ">0 kWh<" in _article(values, "Reaction / dissociation component")
    assert _missing("malformed") in _article(
        values, "Terminal-timestep scoped combined energy"
    )
    assert "Emitted breakdown is empty; no components are inferred." in _article(
        values, "Cumulative emitted component breakdown"
    )
    assert "Emitted breakdown is malformed; no components are inferred." in _article(
        values, "Terminal-timestep diagnostic evaporation breakdown"
    )
    for article in _articles(values):
        assert "<b>Scope</b> · Emitted empty" in article
        assert "<b>Furnace heat</b> · Malformed (object)" in article


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
    cumulative = _article(html, "Cumulative emitted component breakdown")
    assert _row(cumulative, "&lt;script&gt;alert(3)&lt;/script&gt;") == _expected_row(
        "&lt;script&gt;alert(3)&lt;/script&gt;", "2 kWh"
    )
