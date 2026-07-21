from __future__ import annotations

import html as html_lib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p6-mre.js"
_AUTO_ROWS = object()


def _render(artifact: object, rows: object = _AUTO_ROWS) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const rows = process.argv[5] === ""
  ? (artifact && Array.isArray(artifact.timesteps)
      ? artifact.timesteps.map((step) => step && step.summary)
      : [])
  : JSON.parse(process.argv[5]);
const panel = context.ReportPanels.find((entry) => entry.id === "sec-p6-mre");
process.stdout.write(panel.render(artifact, rows, [], {}));
"""
    rows_json = "" if rows is _AUTO_ROWS else json.dumps(rows)
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL), json.dumps(artifact), rows_json],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _species_color(species: str) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = {};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
process.stdout.write(context.ReportLabels.speciesColor(JSON.parse(process.argv[3])));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS), json.dumps(species)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _between(rendered: str, start: str, end: str) -> str:
    assert start in rendered
    remainder = rendered.split(start, 1)[1]
    assert end in remainder
    return remainder.split(end, 1)[0]


def _value_for_label(rendered: str, label: str) -> str:
    match = re.search(
        rf"<span>{re.escape(label)}</span><b>(.*?)</b>", rendered, re.DOTALL
    )
    assert match is not None, f"missing rendered label/value row: {label}"
    return match.group(1)


def _chip_value(rendered: str, label: str) -> str:
    match = re.search(
        rf'<span class="sec-p6-chip(?: [^"]*)?"><b>{re.escape(label)}</b>'
        r"(.*?)</span>",
        rendered,
        re.DOTALL,
    )
    assert match is not None, f"missing rendered chip: {label}"
    return match.group(1)


def _species_card(rendered: str, label: str) -> str:
    for card in re.findall(
        r'<article class="sec-p6-species-card">(.*?)</article>', rendered, re.DOTALL
    ):
        if f">{html_lib.escape(label)}</span>" in card:
            return card
    raise AssertionError(f"missing rendered species card: {label}")


def _species_badge_style(rendered: str, label: str) -> str:
    match = re.search(
        r'<span class="sec-p6-species" style="([^"]+)"><i aria-hidden="true"></i>'
        rf"{re.escape(html_lib.escape(label))}</span>",
        rendered,
    )
    assert match is not None, f"missing rendered species badge: {label}"
    return match.group(1)


def _numeric_measurements(rendered: str) -> list[str]:
    measurements = []
    for fragment in re.findall(r"<b(?: [^>]*)?>(.*?)</b>", rendered, re.DOTALL):
        text = re.sub(r"<[^>]+>", "", fragment)
        numeric_measurement = (
            r"(?:^|\s)[+-]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
            r"(?:e[+-]?\d+)?\s*(?:V|A|kg/h|kg|mol|bar|°C|K)\b"
        )
        if re.search(numeric_measurement, text, re.IGNORECASE):
            measurements.append(text)
    return measurements


def _ladder() -> dict:
    return {
        "schema": "c5_ellingham_ladder_diagnostic_v1",
        "certification": "diagnostic_uncertified",
        "authority": "authoritative_ellingham_graph_with_static_fallback",
        "activity_basis": "gamma_x_single_cation_cleaned_melt_account",
        "temperature_C": 1225.0,
        "temperature_K": 1498.15,
        "pO2_bar": 0.05,
        "declared_rung_V": 0.39,
        "rung_species": ["FeO", "SiO2"],
        "derived_Ed_V": {"FeO": 1.006},
        "delta_vs_declared_rung_V": {"FeO": 0.616},
        "reordering": {
            "ordering_divergence_detected": False,
            "other_species_below_declared_rung": [],
            "derived_order_by_Ed": ["FeO", "SiO2"],
            "declared_order_by_static_voltage": ["FeO", "SiO2"],
        },
        "non_authoritative_voltage_by_oxide": {
            "MnO": {
                "authority": "ellingham_graph",
                "authoritative": False,
                "status": "diagnostic_reconstructed_mn_row_not_authoritative_for_mre",
                "static_declared_V": 1.42,
            }
        },
        "species": {
            "FeO": {
                "ellingham_species": "Fe",
                "static_declared_V": 0.91,
                "oxide_activity": 0.05,
                "oxide_activity_model": "gamma_x_single_cation",
                "inventory_present": True,
                "derived_Ed_V": 1.006,
                "delta_vs_declared_rung_V": 0.616,
                "delta_vs_static_declared_V": 0.096,
                "declared_after_held_rung": True,
                "voltage_authority": "ellingham_graph",
                "voltage_authoritative": True,
                "status": "ok",
            }
        },
    }


def _artifact(summary: dict, final_state: dict | None = None) -> dict:
    return {
        "timesteps": [{"hour": 42, "summary": summary}],
        "terminal": {"final_state": final_state or {}},
    }


def test_p6_renders_emitted_ladder_yield_o2_and_pending_direct_readouts() -> None:
    summary = {
        "mre_ellingham_ladder_diagnostic": _ladder(),
        "mre_uncertified_yield": {
            "FeO": {
                "source_species": "Fe2O3",
                "produced_species": "FeO",
                "produced_kg": 1.25,
                "produced_mol": 17.4,
                "certification": "uncertified_ferric_to_ferrous_reference",
                "reference_V": 0.65,
                "reference_status": "uncertified_heuristic_reference_not_raw_thermo",
                "reason": "Heuristic reference only",
            }
        },
    }
    html = _render(
        _artifact(summary, {"terminal.oxygen_mre_anode_stored": {"O2": 42.0}})
    )
    ladder_region = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<h3>Latest emitted uncertified yield evidence</h3>",
    )
    derived_region = _between(
        ladder_region,
        "<h4>Derived versus declared evidence</h4>",
        "<h4>Emitted reordering object</h4>",
    )
    yield_region = _between(
        html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-block sec-p6-o2">',
    )
    o2_region = _between(
        html,
        '<div class="sec-p6-block sec-p6-o2">',
        '<div class="sec-p6-direct"',
    )
    direct_region = _between(html, '<div class="sec-p6-direct"', "</section>")
    ladder_header = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        '<div class="sec-p6-headline">',
    )
    derived_row = _between(derived_region, "<tbody><tr>", "</tr>")

    assert 'id="sec-p6-mre"' in html
    assert _chip_value(ladder_header, "Certification") == "diagnostic_uncertified"
    assert _chip_value(ladder_header, "Authority") == (
        "authoritative_ellingham_graph_with_static_fallback"
    )
    assert "SiO₂" in _value_for_label(ladder_region, "Held-rung species")
    assert "0.39 V" in _value_for_label(ladder_region, "Declared accounting rung")
    assert re.findall(r'<td class="num">(.*?)</td>', derived_row) == [
        "1.006 V",
        "0.616 V",
    ]
    assert "1.25 kg" in _value_for_label(yield_region, "Produced amount · kg")
    assert "17.4 mol" in _value_for_label(yield_region, "Produced amount · mol")
    assert _value_for_label(yield_region, "Certification") == (
        "uncertified_ferric_to_ferrous_reference"
    )
    assert _value_for_label(yield_region, "Reference status") == (
        "uncertified_heuristic_reference_not_raw_thermo"
    )
    assert _numeric_measurements(yield_region) == ["1.25 kg", "17.4 mol", "0.65 V"]
    assert (
        "Per-hour bookkeeping only; emitted kg and mol stay separate and no "
        "cross-hour total is derived."
        in yield_region
    )
    assert not re.search(r"recovered|collector|collected", yield_region, re.IGNORECASE)
    assert _value_for_label(o2_region, "O₂ stored · MRE anode") == "42 mol"
    assert "recovered" not in o2_region.lower()
    assert "mol-native" in o2_region
    assert _value_for_label(direct_region, "Applied cell voltage · V") == (
        "Pending producer"
    )
    assert _value_for_label(direct_region, "Cell current · A") == "Pending producer"
    assert _value_for_label(direct_region, "Metals production rate · kg/h") == (
        "Pending producer"
    )
    assert _numeric_measurements(direct_region) == []


def test_p6_absent_subtrees_have_separate_pending_states_without_zeroes() -> None:
    html = _render(_artifact({}))

    assert "Pending ladder evidence" in html
    assert "Pending uncertified yield" in html
    assert "Pending MRE-anode O₂" in html
    assert html.count("Pending producer") == 3
    assert _numeric_measurements(html) == []


def test_p6_surfaces_nested_authority_uncertainty_and_escapes_values() -> None:
    hostile_yield_key = "<svg onload=alert(document.domain)>"
    hostile_product = "<img src=x onerror=alert(document.domain)>"
    hostile_extra_key = "</SPAN><IMG SRC=X ONERROR=ALERT(1)>"
    ladder = _ladder()
    ladder["species"]["FeO"][hostile_extra_key] = "ladder marker"
    ladder["non_authoritative_voltage_by_oxide"]["MnO"]["status"] = (
        "<script>authority breach</script>"
    )
    summary = {
        "mre_ellingham_ladder_diagnostic": ladder,
        "mre_uncertified_yield": {
            hostile_yield_key: {
                "source_species": "Fe2O3",
                "produced_species": hostile_product,
                "produced_kg": 0.2,
                "produced_mol": 2.8,
                "certification": "uncertified",
                "reference_V": 0.65,
                "reference_status": "provisional",
                "reason": "A & B",
                "authoritative": False,
                "diagnostic_only": True,
                "high_uncertainty": True,
                "extrapolation": "outside_reference_range",
                "status": "provisional",
                "basis": "per_tick",
                "source": "builtin",
                "reference": "heuristic",
                "skip_reason": "none",
                hostile_extra_key: "yield marker",
            }
        },
    }
    html = _render(
        _artifact(summary, {"terminal.oxygen_mre_anode_stored": {"O2": 4.0}})
    )
    ladder_region = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<h3>Latest emitted uncertified yield evidence</h3>",
    )
    yield_region = _between(
        html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-block sec-p6-o2">',
    )
    feo_card = _species_card(ladder_region, "FeO")
    yield_card = re.findall(
        r'<article class="sec-p6-yield-card">(.*?)</article>', yield_region, re.DOTALL
    )[0]
    encoded_extra_key = html_lib.escape(hostile_extra_key)

    assert _value_for_label(feo_card, "Voltage Authority") == "ellingham_graph"
    assert _value_for_label(feo_card, "Voltage Authoritative") == "true"
    assert _value_for_label(feo_card, "Status") == "ok"
    # P2: per-oxide non-authoritative Authority chip is bound to the MnO row,
    # not merely the duplicate ellingham_graph token on FeO Voltage Authority.
    non_auth_region = _between(
        ladder_region,
        "<h4>Emitted non-authoritative voltage entries</h4>",
        "<h4>Per-species diagnostic rows</h4>",
    )
    mno_row = re.search(
        r'<div class="sec-p6-authority-row">.*?MnO.*?</div></div>',
        non_auth_region,
        re.DOTALL,
    )
    assert mno_row, "MnO non-authoritative row missing"
    mno_html = mno_row.group(0)
    assert '<b>Authority</b>ellingham_graph</span>' in mno_html
    assert '<b>Authoritative</b>false</span>' in mno_html
    assert "Static declared voltage</span><b>1.42 V</b>" in mno_html
    assert (
        '<b>Authoritative</b>false</span><span class="sec-p6-chip sec-p6-warn">'
        '<b>Status</b>&lt;script&gt;authority breach&lt;/script&gt;'
        in ladder_region
    )
    assert _value_for_label(yield_region, "Authoritative") == "false"
    assert _value_for_label(yield_region, "Diagnostic only") == "true"
    assert _value_for_label(yield_region, "High uncertainty") == "true"
    assert _value_for_label(yield_region, "Extrapolation") == "outside_reference_range"
    assert _value_for_label(yield_region, "Status") == "provisional"
    assert _value_for_label(yield_region, "Basis") == "per_tick"
    assert _value_for_label(yield_region, "Source") == "builtin"
    assert _value_for_label(yield_region, "Reference") == "heuristic"
    assert _value_for_label(yield_region, "Skip reason") == "none"
    assert _value_for_label(yield_region, "Reason") == "A &amp; B"
    assert "&amp;amp;" not in yield_region
    assert html_lib.escape(hostile_yield_key) in yield_region
    assert html_lib.escape(hostile_product) in _value_for_label(
        yield_region, "Produced species"
    )
    assert _species_badge_style(ladder_region, "FeO") == (
        f"--species-color:{_species_color('FeO')}"
    )
    assert _species_badge_style(ladder_region, "SiO₂") == (
        f"--species-color:{_species_color('SiO2')}"
    )
    assert encoded_extra_key in feo_card
    assert encoded_extra_key in yield_card
    assert hostile_extra_key not in ladder_region
    assert hostile_extra_key not in yield_region
    assert "&amp;lt;/SPAN&amp;gt;" not in html
    assert "&lt;script&gt;authority breach&lt;/script&gt;" in ladder_region
    assert "<script>authority breach</script>" not in html
    assert hostile_yield_key not in html
    assert hostile_product not in html

    # Flip path: inverted uncertainty flags must render their emitted values,
    # not stay green via unscoped ladder-side "true" tokens.
    flip_html = _render(
        _artifact(
            {
                "mre_ellingham_ladder_diagnostic": _ladder(),
                "mre_uncertified_yield": {
                    "FeO": {
                        "source_species": "Fe2O3",
                        "produced_species": "FeO",
                        "produced_kg": 0.1,
                        "produced_mol": 1.0,
                        "certification": "uncertified",
                        "reference_V": 0.65,
                        "reference_status": "provisional",
                        "reason": "flip fixture",
                        "authoritative": True,
                        "diagnostic_only": False,
                        "high_uncertainty": False,
                    }
                },
            }
        )
    )
    flip_yield = _between(
        flip_html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-direct"',
    )
    assert _value_for_label(flip_yield, "Authoritative") == "true"
    assert _value_for_label(flip_yield, "Diagnostic only") == "false"
    assert _value_for_label(flip_yield, "High uncertainty") == "false"

    false_ladder = _ladder()
    false_ladder["species"]["FeO"]["voltage_authoritative"] = False
    false_html = _render(
        _artifact({"mre_ellingham_ladder_diagnostic": false_ladder})
    )
    false_ladder_region = _between(
        false_html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<strong>Pending uncertified yield</strong>",
    )
    false_feo_card = _species_card(false_ladder_region, "FeO")
    assert _value_for_label(false_feo_card, "Voltage Authoritative") == "false"


def test_p6_partial_inputs_stay_pending_and_are_not_derived_or_substituted() -> None:
    ladder = {
        "schema": "partial",
        "certification": "diagnostic_uncertified",
        "authority": "read_only",
        "activity_basis": "cleaned_melt",
        "temperature_C": 1000.0,
        "pO2_bar": 0.01,
        "declared_rung_V": 1.1,
        "rung_species": ["FeO"],
        "derived_Ed_V": {"FeO": 1.5},
        "species": {
            "FeO": {
                "static_declared_V": 1.2,
                "derived_Ed_V": 1.5,
            }
        },
    }
    summary = {
        "mre_ellingham_ladder_diagnostic": ladder,
        "mre_uncertified_yield": {
            "FeO": {
                "source_species": "Fe2O3",
                "produced_species": "FeO",
                "produced_mol": 1.0,
                "certification": "uncertified",
                "reference_V": 0.65,
                "reference_status": "provisional",
                "reason": "partial fixture",
            }
        },
    }
    html = _render(
        _artifact(summary, {"terminal.oxygen_melt_offgas_stored": {"O2": 99.0}})
    )
    ladder_region = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<h3>Latest emitted uncertified yield evidence</h3>",
    )
    yield_region = _between(
        html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-direct"',
    )
    derived_region = _between(
        ladder_region,
        "<h4>Derived versus declared evidence</h4>",
        "<h4>Emitted reordering object</h4>",
    )
    derived_row = _between(derived_region, "<tbody><tr>", "</tr>")
    species_card = _species_card(ladder_region, "FeO")
    o2_pending_region = _between(
        html, "<strong>Pending MRE-anode O₂</strong>", '<div class="sec-p6-direct"'
    )
    direct_region = _between(html, '<div class="sec-p6-direct"', "</section>")

    assert "Delta vs declared · V" in ladder_region
    assert "1.5 V" in derived_row and "not emitted" in derived_row
    assert "0.4 V" not in derived_region
    assert "not emitted" in _value_for_label(
        species_card, "Delta Vs Declared Rung V"
    )
    assert "not emitted" in _value_for_label(
        species_card, "Delta Vs Static Declared V"
    )
    assert "0.4 V" not in species_card and "0.3 V" not in species_card
    assert "not emitted" in _value_for_label(ladder_region, "Temperature")
    assert "1273 K" not in ladder_region and "1,273 K" not in ladder_region
    assert "not emitted" in _value_for_label(yield_region, "Produced amount · kg")
    assert "0.07184 kg" not in yield_region
    assert "Pending reordering evidence" in ladder_region
    assert "Pending non-authoritative voltage entries" in ladder_region
    assert "not emitted in terminal.final_state" in o2_pending_region
    assert "99 mol" not in o2_pending_region
    assert _value_for_label(direct_region, "Applied cell voltage · V") == (
        "Pending producer"
    )
    assert _value_for_label(direct_region, "Cell current · A") == "Pending producer"
    assert _value_for_label(direct_region, "Metals production rate · kg/h") == (
        "Pending producer"
    )

    kg_only_html = _render(
        _artifact(
            {
                "mre_uncertified_yield": {
                    "FeO": {
                        "source_species": "Fe2O3",
                        "produced_species": "FeO",
                        "produced_kg": 0.07184,
                        "certification": "uncertified",
                        "reference_V": 0.65,
                        "reference_status": "provisional",
                        "reason": "inverse partial fixture",
                    }
                }
            }
        )
    )
    kg_only_yield = _between(
        kg_only_html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        '<div class="sec-p6-direct"',
    )
    assert "not emitted" in _value_for_label(
        kg_only_yield, "Produced amount · mol"
    )
    assert "1 mol" not in kg_only_yield

    k_only_html = _render(
        _artifact(
            {
                "mre_ellingham_ladder_diagnostic": {
                    "schema": "k-only-partial",
                    "temperature_K": 1498.15,
                }
            }
        )
    )
    k_only_ladder = _between(
        k_only_html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<strong>Pending uncertified yield</strong>",
    )
    k_only_temperature = _value_for_label(k_only_ladder, "Temperature")
    assert "not emitted" in k_only_temperature and "1,498 K" in k_only_temperature
    assert "1225 °C" not in k_only_temperature

    species_only_html = _render(
        _artifact(
            {
                "mre_ellingham_ladder_diagnostic": {
                    "schema": "species-only-partial",
                    "rung_species": ["FeO"],
                    "species": {
                        "FeO": {
                            "static_declared_V": 0.91,
                            "derived_Ed_V": 1.5,
                        }
                    },
                }
            }
        )
    )
    species_only_ladder = _between(
        species_only_html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<strong>Pending uncertified yield</strong>",
    )
    species_only_headline = _between(
        species_only_ladder,
        '<div class="sec-p6-headline">',
        '<details class="sec-p6-disclosure">',
    )
    species_only_derived = _between(
        species_only_ladder,
        "<h4>Derived versus declared evidence</h4>",
        "<h4>Emitted reordering object</h4>",
    )
    assert "not emitted" in _value_for_label(
        species_only_headline, "Declared accounting rung"
    )
    assert "0.91 V" not in species_only_headline
    assert "Pending derived-versus-declared detail" in species_only_derived
    assert "1.5 V" not in species_only_derived


def test_p6_renders_latest_owned_evidence_and_failure_status() -> None:
    older_ladder = _ladder()
    older_ladder["declared_rung_V"] = 0.11
    older_ladder["status"] = "older-status"
    latest_ladder = _ladder()
    latest_ladder["declared_rung_V"] = 0.77
    latest_ladder["status"] = "diagnostic_failed:ValueError"
    rows = [
        {
            "mre_ellingham_ladder_diagnostic": older_ladder,
            "mre_uncertified_yield": {"FeO": {"certification": "older"}},
        },
        {},
        {
            "mre_ellingham_ladder_diagnostic": latest_ladder,
            "mre_uncertified_yield": {"FeO": {"certification": "latest"}},
        },
    ]

    html = _render(_artifact({}), rows)
    ladder_region = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<h3>Latest emitted uncertified yield evidence</h3>",
    )
    ladder_header = _between(
        html,
        "<h3>Latest emitted ladder evidence</h3>",
        '<div class="sec-p6-headline">',
    )
    yield_region = _between(
        html,
        "<h3>Latest emitted uncertified yield evidence</h3>",
        "<strong>Pending MRE-anode O₂</strong>",
    )

    assert _chip_value(ladder_header, "Status") == "diagnostic_failed:ValueError"
    assert "0.77 V" in _value_for_label(ladder_region, "Declared accounting rung")
    assert "0.11 V" not in ladder_region
    assert _value_for_label(yield_region, "Certification") == "latest"
    assert "older" not in yield_region


def test_p6_distinguishes_empty_and_malformed_owned_subtrees() -> None:
    empty_html = _render(
        _artifact(
            {
                "mre_ellingham_ladder_diagnostic": {},
                "mre_uncertified_yield": {},
            }
        )
    )
    assert "Empty ladder evidence" in empty_html
    assert "Empty uncertified yield" in empty_html
    assert "Malformed ladder evidence" not in empty_html
    assert "Malformed uncertified yield" not in empty_html

    for malformed in (None, [{}]):
        malformed_html = _render(
            _artifact(
                {
                    "mre_ellingham_ladder_diagnostic": malformed,
                    "mre_uncertified_yield": malformed,
                }
            )
        )
        assert "Malformed ladder evidence" in malformed_html
        assert "Malformed uncertified yield" in malformed_html
        assert "Empty ladder evidence" not in malformed_html
        assert "Empty uncertified yield" not in malformed_html


def test_p6_render_handles_null_rows_summaries_and_malformed_terminal_account() -> None:
    null_html = _render(None, None)

    assert "Pending ladder evidence" in null_html
    assert "Pending uncertified yield" in null_html
    assert "Pending MRE-anode O₂" in null_html
    assert null_html.count("Pending producer") == 3

    malformed_html = _render(
        {
            "terminal": {
                "final_state": {"terminal.oxygen_mre_anode_stored": []}
            }
        },
        [None, "malformed summary", {}],
    )

    assert "Pending ladder evidence" in malformed_html
    assert "Pending uncertified yield" in malformed_html
    assert "Malformed MRE-anode O₂" in malformed_html
    assert _numeric_measurements(malformed_html) == []

    empty_ladder = _ladder()
    empty_ladder["rung_species"] = []
    zero_html = _render(
        _artifact(
            {"mre_ellingham_ladder_diagnostic": empty_ladder},
            {"terminal.oxygen_mre_anode_stored": {"O2": 0.0}},
        )
    )
    zero_ladder_region = _between(
        zero_html,
        "<h3>Latest emitted ladder evidence</h3>",
        "<strong>Pending uncertified yield</strong>",
    )
    zero_o2_region = _between(
        zero_html,
        '<div class="sec-p6-block sec-p6-o2">',
        '<div class="sec-p6-direct"',
    )

    assert "empty emitted list" in _value_for_label(
        zero_ladder_region, "Held-rung species"
    )
    assert "0 mol" in zero_o2_region
    assert "Pending MRE-anode O₂" not in zero_html
    assert "Malformed MRE-anode O₂" not in zero_html
