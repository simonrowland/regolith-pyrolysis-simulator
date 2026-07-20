from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "web/report_viewer/labels.js"
PANEL = ROOT / "web/report_viewer/panels/p2-taps.js"


def _render(artifact: dict) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const rows = (artifact.timesteps || []).map((timestep) => timestep.summary || {});
process.stdout.write(context.ReportPanels[0].render(artifact, rows, [], {}));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL), json.dumps(artifact)],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _between(html: str, start_marker: str, end_marker: str) -> str:
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def _tap_card(html: str, card_id: str) -> str:
    marker = f'<article class="card sec-p2-card" data-p2-card="{card_id}">'
    return _between(html, marker, "</article>")


def _species_map_region(card_html: str, heading: str) -> str:
    marker = f'<h3>{heading}</h3><div class="sec-p2-species-list">'
    return _between(card_html, marker, "</div>")


def _interface_row(html: str, label: str) -> str:
    marker = f'<div class="sec-p2-kv"><span>{label}</span><b>'
    return _between(html, marker, "</div>")


def _container_notice(html: str, region: str) -> str:
    marker = (
        '<div class="pending sec-p2-inline-pending" '
        f'data-p2-container="{region}">'
    )
    return _between(html, marker, "</div>")


def _artifact(*, stratification: dict | None, final_state: dict | None = None) -> dict:
    summary = {}
    if stratification is not None:
        summary["metal_phase_stratification"] = stratification
    return {
        "artifact_schema_version": "0.2.0",
        "header": {"run_id": "p2-test"},
        "timesteps": [{"hour": 12, "summary": summary}],
        "terminal": {"final_state": final_state or {}},
    }


def _present_stratification() -> dict:
    return {
        "status": "diagnostic_only_no_tap_gate",
        "mode": "stratify",
        "existing_extraction_behavior": "unchanged_diagnostic_only",
        "authoritative": False,
        "diagnostic_only": True,
        "extrapolation": True,
        "high_uncertainty": True,
        "temperature_K": 1673.15,
        "melt_density_kg_m3": 2700.0,
        "melt_density_tier": "fallback_basaltic_melt_constant_engine_density_unavailable",
        "melt_density_fallback_engaged": True,
        "provenance": {
            "account_state_source": "builtin-authored",
            "diagnostic_derivation": "builtin-committed",
        },
        "pools": {
            "float_layer": {
                "high_uncertainty": False,
                "species_mol": {"Al": 7.0, "Si": 1.0},
                "composition_wt_pct": {"Al": 88.25, "Si": 11.75},
                "density_kg_m3": 2240.0,
                "buoyancy": {
                    "verdict": "float",
                    "alloy_density_kg_m3": 2240.0,
                    "melt_density_kg_m3": 2700.0,
                    "delta_rho_kg_m3": -460.0,
                    "ambiguity_threshold_kg_m3": 150.0,
                    "melt_density_uncertainty_kg_m3": 135.0,
                    "alloy_density_uncertainty_kg_m3": 20.0,
                },
                "density_correlation_provenance": {
                    "Al": {
                        "source": "Assael density source",
                        "valid_range_K": [933.0, 1190.0],
                        "temperature_K": 1673.15,
                        "status": "extrapolated_above_valid_range",
                    }
                },
            },
            "bottom_pool": {
                "species_mol": {"Fe": 100.0, "Co": 2.0, "Ni": 1.0},
                "composition_wt_pct": {"Fe": 97.1, "Co": 1.9, "Ni": 1.0},
                "density_kg_m3": 7200.0,
                "buoyancy": {
                    "verdict": "sink",
                    "alloy_density_kg_m3": 7200.0,
                    "melt_density_kg_m3": 2700.0,
                    "delta_rho_kg_m3": 4500.0,
                    "ambiguity_threshold_kg_m3": 260.0,
                    "melt_density_uncertainty_kg_m3": 135.0,
                    "alloy_density_uncertainty_kg_m3": 230.0,
                },
                "density_correlation_provenance": {
                    "Fe": {
                        "source": "Assael iron source",
                        "valid_range_K": [1809.0, 2480.0],
                        "temperature_K": 1673.15,
                        "status": "extrapolated_below_valid_range",
                    }
                },
            },
        },
        "unclassified_staging_mol": {"Co": 0.4, "W": 0.1},
        "interface": {
            "area_m2": 0.2,
            "float_layer_mass_kg": 0.25,
            "equivalent_film_thickness_m": 0.0005,
            "coverage_reference_thickness_m": 0.001,
            "coverage_fraction_at_reference_thickness": 0.5,
            "aggressive_float_tap_assumption_load_bearing": True,
        },
    }


def test_p2_renders_two_taps_contaminants_accounts_and_geometry() -> None:
    html = _render(_artifact(
        stratification=_present_stratification(),
        final_state={
            "process.metal_phase_float_layer": {"Al": 70.0, "Si": 10.0},
            "process.metal_phase_bottom_pool": {"Fe": 1000.0, "Co": 20.0, "Ni": 10.0},
        },
    ))
    upper = _tap_card(html, "float")
    bottom = _tap_card(html, "bottom")
    upper_pool = _species_map_region(upper, "Stratified pool · mol by species")
    upper_terminal = _species_map_region(upper, "Terminal ledger account · mol by species")
    bottom_pool = _species_map_region(bottom, "Stratified pool · mol by species")
    bottom_terminal = _species_map_region(bottom, "Terminal ledger account · mol by species")

    assert 'id="sec-p2-taps"' in html
    assert "Upper float tap" in upper
    assert "Bottom pool tap" in bottom
    assert 'Al</b> <span title="7 mol">7 mol</span>' in upper_pool
    assert 'Fe</b> <span title="100 mol">100 mol</span>' in bottom_pool
    assert "<b>Fe</b>" not in upper_pool
    assert "<b>Al</b>" not in bottom_pool
    assert 'Al</b> <span title="70 mol">70 mol</span>' in upper_terminal
    assert 'Fe</b> <span title="1000 mol">1,000 mol</span>' in bottom_terminal
    assert "<b>Fe</b>" not in upper_terminal
    assert "<b>Al</b>" not in bottom_terminal
    assert "Fe 97.1 wt% · largest emitted share" in bottom
    assert 'Co contaminant</b> <span title="1.9 wt%">1.9 wt%</span>' in bottom
    assert 'Ni contaminant</b> <span title="1 wt%">1 wt%</span>' in bottom
    assert "Equivalent film thickness" in html and "5.00e-4 m" in html
    assert "Coverage fraction at reference thickness" in html and ">0.5<" in html
    assert "Frozen kg tap views pending producer attach" in html


def test_p2_absent_stratification_stays_pending_without_hiding_terminal_mol() -> None:
    html = _render(_artifact(
        stratification=None,
        final_state={"process.metal_phase_bottom_pool": {"Fe": 3.0}},
    ))
    state = _between(
        html,
        '<div class="sec-p2-state" data-p2-region="state">',
        '<div class="sec-p2-grid">',
    )
    upper_grade = _between(
        _tap_card(html, "float"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    bottom_grade = _between(
        _tap_card(html, "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )

    assert "Pending metal-phase stratification" in html
    assert "Elemental tap grade pending" in html
    assert "Pool mol amounts are not converted into a grade" in html
    assert 'Fe</b> <span title="3 mol">3 mol</span>' in html
    assert "Emitted temperature</span><b>not emitted</b>" in state
    assert "Emitted melt density</span><b>not emitted</b>" in state
    assert " wt%" not in upper_grade
    assert " wt%" not in bottom_grade

    partial = _present_stratification()
    partial.pop("temperature_K")
    partial.pop("melt_density_kg_m3")
    partial_html = _render(_artifact(stratification=partial))
    partial_state = _between(
        partial_html,
        '<div class="sec-p2-state" data-p2-region="state">',
        '<div class="sec-p2-grid">',
    )
    assert "Emitted temperature</span><b>not emitted</b>" in partial_state
    assert "Emitted melt density</span><b>not emitted</b>" in partial_state
    assert "1673.15 K" not in partial_state
    assert "2,700 kg/m³" not in partial_state


def test_p2_partial_composition_does_not_derive_wt_pct_from_species_mol() -> None:
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"].pop("composition_wt_pct")
    stratification["pools"]["bottom_pool"]["species_mol"] = {"Fe": 2.0, "Co": 1.0}

    html = _render(_artifact(stratification=stratification))
    bottom_grade = _between(
        _tap_card(html, "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )

    assert "Elemental composition by weight was not emitted" in bottom_grade
    assert "Pool mol amounts are not converted into a grade" in bottom_grade
    assert " wt%" not in bottom_grade
    assert 'Fe</b> <span title="2 mol">2 mol</span>' in _tap_card(html, "bottom")
    assert 'Co</b> <span title="1 mol">1 mol</span>' in _tap_card(html, "bottom")


def test_p2_partial_interface_does_not_derive_geometry() -> None:
    cases = (
        ("area_m2", "Interface area", "0.2232 m²"),
        ("float_layer_mass_kg", "Float-layer mass", "0.224 kg"),
        ("equivalent_film_thickness_m", "Equivalent film thickness", "5.58e-4 m"),
        ("coverage_reference_thickness_m", "Coverage reference thickness", "0.001 m"),
        ("coverage_fraction_at_reference_thickness", "Coverage fraction at reference thickness", ">0.5<"),
    )

    for field, label, derivable_value in cases:
        stratification = _present_stratification()
        stratification["interface"].pop(field)
        html = _render(_artifact(stratification=stratification))
        row = _interface_row(html, label)

        assert f"{label}</span><b>not emitted</b>" in row
        assert derivable_value not in row
        assert "</span><b><span" not in row


def test_p2_surfaces_diagnostic_authority_and_load_bearing_flags() -> None:
    html = _render(_artifact(stratification=_present_stratification()))
    top_flags = _between(
        html,
        '<div class="sec-p2-flags" data-p2-flags="stratification">',
        "</div>",
    )
    pool_flags = _between(
        _tap_card(html, "float"),
        '<div class="sec-p2-flags" data-p2-flags="pool">',
        "</div>",
    )

    assert "Diagnostic only · no tap gate" in html
    assert "Extraction behavior unchanged · diagnostic only" in html
    assert "Fallback basaltic-melt constant · engine density unavailable" in html
    assert "Melt-density fallback engaged:</b> true" in html
    assert "Aggressive float-tap assumption: load-bearing" in html
    assert "Aggressive float-tap assumption</span><b>load-bearing" in html
    assert "Extrapolated below valid range" in html
    assert "Assael iron source" in html
    assert "Authoritative:</b> false" in top_flags
    assert "Diagnostic only:</b> true" in top_flags
    assert "Extrapolation:</b> true" in top_flags
    assert "High uncertainty:</b> true" in top_flags
    assert "High uncertainty:</b> false" in pool_flags

    empty_status = _present_stratification()
    empty_status["status"] = ""
    empty_status_flags = _between(
        _render(_artifact(stratification=empty_status)),
        '<div class="sec-p2-flags" data-p2-flags="stratification">',
        "</div>",
    )
    assert "Diagnostic status:</b> empty" in empty_status_flags

    malformed_status = _present_stratification()
    malformed_status["status"] = {}
    malformed_status_flags = _between(
        _render(_artifact(stratification=malformed_status)),
        '<div class="sec-p2-flags" data-p2-flags="stratification">',
        "</div>",
    )
    assert "Diagnostic status:</b> malformed" in malformed_status_flags

    missing_assumption = _present_stratification()
    missing_assumption["interface"].pop("aggressive_float_tap_assumption_load_bearing")
    missing_assumption_html = _render(_artifact(stratification=missing_assumption))
    assert "Aggressive float-tap assumption: not emitted" in _tap_card(
        missing_assumption_html,
        "float",
    )
    assert "Aggressive float-tap assumption</span><b>not emitted</b>" in _interface_row(
        missing_assumption_html,
        "Aggressive float-tap assumption",
    )

    malformed_assumption = _present_stratification()
    malformed_assumption["interface"]["aggressive_float_tap_assumption_load_bearing"] = 0
    malformed_assumption_html = _render(_artifact(stratification=malformed_assumption))
    assert "Aggressive float-tap assumption: malformed" in _tap_card(
        malformed_assumption_html,
        "float",
    )
    assert "Aggressive float-tap assumption</span><b>malformed</b>" in _interface_row(
        malformed_assumption_html,
        "Aggressive float-tap assumption",
    )


def test_p2_distinguishes_grade_absent_empty_malformed_and_zero_states() -> None:
    present_html = _render(_artifact(stratification=_present_stratification()))
    present_contaminants = _between(
        _tap_card(present_html, "float"),
        '<div class="sec-p2-contaminants"',
        '<div class="sec-p2-kv">',
    )
    assert "Co not present in emitted grade" in present_contaminants
    assert "Ni not present in emitted grade" in present_contaminants
    assert "contaminant grade not emitted" not in present_contaminants

    absent = _present_stratification()
    absent["pools"]["bottom_pool"].pop("composition_wt_pct")
    absent_grade = _between(
        _tap_card(_render(_artifact(stratification=absent)), "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    assert "Elemental composition by weight was not emitted" in absent_grade
    assert "Co and Ni contaminant grades not emitted" in absent_grade

    empty = _present_stratification()
    empty["pools"]["bottom_pool"]["composition_wt_pct"] = {}
    empty_grade = _between(
        _tap_card(_render(_artifact(stratification=empty)), "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    assert "No species present in emitted grade" in empty_grade
    assert "Co not present in emitted grade" in empty_grade

    malformed = _present_stratification()
    malformed["pools"]["bottom_pool"]["composition_wt_pct"] = []
    malformed_grade = _between(
        _tap_card(_render(_artifact(stratification=malformed)), "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    assert "Emitted elemental composition by weight is not a species map" in malformed_grade
    assert "contaminant grades unavailable · emitted grade malformed" in malformed_grade

    mixed_malformed = _present_stratification()
    mixed_malformed["pools"]["bottom_pool"]["composition_wt_pct"] = {
        "Fe": "bad",
        "Co": 1.0,
    }
    mixed_malformed_card = _tap_card(
        _render(_artifact(stratification=mixed_malformed)),
        "bottom",
    )
    assert "Elemental tap grade malformed" in mixed_malformed_card
    assert "largest emitted share" not in mixed_malformed_card

    zero = _present_stratification()
    zero["pools"]["bottom_pool"]["composition_wt_pct"] = {"Co": 0.0}
    zero_grade = _between(
        _tap_card(_render(_artifact(stratification=zero)), "bottom"),
        '<div class="sec-p2-grade"',
        '<div class="sec-p2-kv">',
    )
    assert 'Co contaminant</b> <span title="0 wt%">0 wt%</span>' in zero_grade
    assert "Ni not present in emitted grade" in zero_grade


def test_p2_renders_unclassified_staging_as_a_distinct_literal_bin() -> None:
    present_html = _render(_artifact(stratification=_present_stratification()))
    present = _between(
        present_html,
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "Unclassified metal staging · mol by species" in present
    assert 'Co</b> <span title="0.4 mol">0.4 mol</span>' in present
    assert 'W</b> <span title="0.1 mol">0.1 mol</span>' in present
    assert 'title="0.4 mol"' not in _tap_card(present_html, "bottom")

    absent = _present_stratification()
    absent.pop("unclassified_staging_mol")
    absent_region = _between(
        _render(_artifact(stratification=absent)),
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "Unclassified metal staging mol map not emitted" in absent_region

    empty = _present_stratification()
    empty["unclassified_staging_mol"] = {}
    empty_region = _between(
        _render(_artifact(stratification=empty)),
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "No unclassified species present in emitted map" in empty_region

    malformed = _present_stratification()
    malformed["unclassified_staging_mol"] = []
    malformed_region = _between(
        _render(_artifact(stratification=malformed)),
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert "Unclassified metal staging mol map is malformed" in malformed_region

    zero = _present_stratification()
    zero["unclassified_staging_mol"] = {"Co": 0.0}
    zero_region = _between(
        _render(_artifact(stratification=zero)),
        '<details class="card sec-p2-interface sec-p2-unclassified" data-p2-region="unclassified">',
        "</details>",
    )
    assert 'Co</b> <span title="0 mol">0 mol</span>' in zero_region


def test_p2_newest_malformed_stratification_does_not_fall_back_to_older_map() -> None:
    artifact = _artifact(stratification=None)
    artifact["timesteps"] = [
        {
            "hour": 11,
            "summary": {"metal_phase_stratification": _present_stratification()},
        },
        {
            "hour": 12,
            "summary": {"metal_phase_stratification": []},
        },
    ]

    html = _render(artifact)
    assert 'data-p2-container="stratification"' in html
    malformed = _between(
        html,
        '<div class="pending sec-p2-panel-pending" '
        'data-p2-container="stratification">',
        "</div>",
    )

    assert "Latest emitted stratification (hour 12)" in html
    assert "Malformed metal-phase stratification" in malformed
    assert "Older timestep reports are not substituted" in malformed
    assert "1673.15 K" not in html
    assert "Al 88.25 wt% · largest emitted share" not in html


def test_p2_distinguishes_enclosing_container_states() -> None:
    cases = (
        ("absent", "Not emitted", "was not emitted"),
        ("empty", "Empty", "map is empty"),
        ("malformed", "Malformed", "is not a map"),
        ("zero", "Malformed", "is not a map"),
    )

    for state, heading, detail in cases:
        pools = _present_stratification()
        if state == "absent":
            pools.pop("pools")
        elif state == "empty":
            pools["pools"] = {}
        elif state == "malformed":
            pools["pools"] = []
        else:
            pools["pools"] = 0
        pools_notice = _container_notice(
            _render(_artifact(stratification=pools)),
            "pools",
        )
        assert f"<strong>{heading}</strong>" in pools_notice
        assert detail in pools_notice

        pool_member = _present_stratification()
        if state == "absent":
            pool_member["pools"].pop("float_layer")
        elif state == "empty":
            pool_member["pools"]["float_layer"] = {}
        elif state == "malformed":
            pool_member["pools"]["float_layer"] = []
        else:
            pool_member["pools"]["float_layer"] = 0
        pool_member_notice = _container_notice(
            _tap_card(_render(_artifact(stratification=pool_member)), "float"),
            "pool-float",
        )
        assert f"<strong>{heading}</strong>" in pool_member_notice
        assert detail in pool_member_notice

        interface = _present_stratification()
        if state == "absent":
            interface.pop("interface")
        elif state == "empty":
            interface["interface"] = {}
        elif state == "malformed":
            interface["interface"] = []
        else:
            interface["interface"] = 0
        interface_html = _render(_artifact(stratification=interface))
        interface_notice = _container_notice(interface_html, "interface")
        assert f"<strong>{heading}</strong>" in interface_notice
        assert detail in interface_notice
        if state in {"malformed", "zero"}:
            assert "Aggressive float-tap assumption: malformed" in _tap_card(
                interface_html,
                "float",
            )

        for field, region in (
            ("buoyancy", "buoyancy"),
            ("density_correlation_provenance", "density-provenance"),
        ):
            nested = _present_stratification()
            if state == "absent":
                nested["pools"]["float_layer"].pop(field)
            elif state == "empty":
                nested["pools"]["float_layer"][field] = {}
            elif state == "malformed":
                nested["pools"]["float_layer"][field] = []
            else:
                nested["pools"]["float_layer"][field] = 0
            nested_notice = _container_notice(
                _tap_card(_render(_artifact(stratification=nested)), "float"),
                region,
            )
            assert f"<strong>{heading}</strong>" in nested_notice
            assert detail in nested_notice

        provenance = _present_stratification()
        if state == "absent":
            provenance.pop("provenance")
        elif state == "empty":
            provenance["provenance"] = {}
        elif state == "malformed":
            provenance["provenance"] = []
        else:
            provenance["provenance"] = 0
        provenance_notice = _container_notice(
            _render(_artifact(stratification=provenance)),
            "provenance",
        )
        assert f"<strong>{heading}</strong>" in provenance_notice
        assert detail in provenance_notice

        final_state_artifact = _artifact(
            stratification=_present_stratification(),
            final_state={"process.metal_phase_bottom_pool": {"Fe": 1.0}},
        )
        if state == "absent":
            final_state_artifact["terminal"].pop("final_state")
        elif state == "empty":
            final_state_artifact["terminal"]["final_state"] = {}
        elif state == "malformed":
            final_state_artifact["terminal"]["final_state"] = []
        else:
            final_state_artifact["terminal"]["final_state"] = 0
        final_state_notice = _container_notice(
            _render(final_state_artifact),
            "final-state",
        )
        assert f"<strong>{heading}</strong>" in final_state_notice
        assert detail in final_state_notice

        terminal_artifact = _artifact(stratification=_present_stratification())
        if state == "absent":
            terminal_artifact.pop("terminal")
        elif state == "empty":
            terminal_artifact["terminal"] = {}
        elif state == "malformed":
            terminal_artifact["terminal"] = []
        else:
            terminal_artifact["terminal"] = 0
        terminal_notice = _container_notice(
            _render(terminal_artifact),
            "terminal",
        )
        expected_terminal_heading = "Empty" if state == "empty" else heading
        assert f"<strong>{expected_terminal_heading}</strong>" in terminal_notice
        assert detail in terminal_notice

    present = _render(_artifact(
        stratification=_present_stratification(),
        final_state={"process.metal_phase_bottom_pool": {"Fe": 1.0}},
    ))
    assert 'data-p2-container="pools"' not in present
    assert 'data-p2-container="pool-float"' not in present
    assert 'data-p2-container="interface"' not in present
    assert 'data-p2-container="buoyancy"' not in present
    assert 'data-p2-container="density-provenance"' not in present
    assert 'data-p2-container="provenance"' not in present
    assert 'data-p2-container="terminal"' not in present
    assert 'data-p2-container="final-state"' not in present


def test_p2_escapes_untrusted_artifact_values() -> None:
    stratification = _present_stratification()
    stratification["pools"]["bottom_pool"]["composition_wt_pct"] = {
        '<img src=x onerror="boom">': 12.5,
    }
    stratification["pools"]["bottom_pool"]["density_correlation_provenance"] = {
        "Fe": {
            "source": '<script>alert("x")</script>',
            "valid_range_K": [1.0, 2.0],
            "temperature_K": 3.0,
            "status": "within_valid_range",
        }
    }

    html = _render(_artifact(stratification=stratification))

    assert '<img src=x onerror="boom">' not in html
    assert '&lt;img src=x onerror=&quot;boom&quot;&gt;' in html
    assert '<script>alert("x")</script>' not in html
    assert '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;' in html
