import json
import re
import subprocess
from pathlib import Path


REPORT_ROOT = Path(__file__).resolve().parents[1] / "web" / "report_viewer"


def _render_panel(artifact: object) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const panel = context.ReportPanels.find((candidate) => candidate.id === "sec-p1-fe-redox");
process.stdout.write(panel.render(JSON.parse(process.argv[4]), [], [], {}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(REPORT_ROOT / "labels.js"),
            str(REPORT_ROOT / "panels" / "p1-fe-redox.js"),
            json.dumps(artifact),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _render_panel_timestep(artifact: object, index: int) -> str:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const selected = { innerHTML: "" };
const context = {
  console,
  document: {
    querySelector(selector) {
      return selector === "#sec-p1-selected-timestep-body" ? selected : null;
    }
  }
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const panel = context.ReportPanels.find((candidate) => candidate.id === "sec-p1-fe-redox");
panel.onTimestep(JSON.parse(process.argv[4]), Number(process.argv[5]));
process.stdout.write(selected.innerHTML);
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(REPORT_ROOT / "labels.js"),
            str(REPORT_ROOT / "panels" / "p1-fe-redox.js"),
            json.dumps(artifact),
            str(index),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout


def _artifact(summary: dict, *, hour: int = 197) -> dict:
    return {
        "artifact_schema_version": "0.2.0",
        "timesteps": [{"hour": hour, "summary": summary}],
    }


def _core_redox(**overrides) -> dict:
    redox = {
        "fO2_log": -8.5,
        "iw_log": -10.0,
        "temperature_K": 1800.0,
        "pressure_bar": 0.001,
        "fe3_over_sigma_fe": 0.2,
        "ferric_frac": 0.2,
        "ferrous_frac": 0.7,
        "native_fe_frac": 0.1,
        "fe2o3_over_feo_molar": 0.25,
        "fe2o3_equiv_wt_pct": 3.2,
        "feo_equiv_wt_pct": 7.8,
        "status": "ok",
        "source": "simulator.fe_redox:kress91_split",
        "reference": "Kress and Carmichael 1991",
        "diagnostic_only": True,
        "temperature_band_case": "within_band",
        "temperature_band_status": "grounded",
        "temperature_band_source": "Kress91 basalt range",
        "authoritative": True,
        "extrapolation": False,
        "high_uncertainty": False,
        "native_fe_saturation": True,
        "native_fe_threshold": "FeO_activity_saturation",
    }
    redox.update(overrides)
    return redox


def _assert_metric_pending(html: str, label: str) -> None:
    pattern = (
        re.escape(f'<div class="k">{label}</div><div class="v">')
        + r'<span class="sec-p1-pending-value">not emitted</span>'
    )
    assert re.search(pattern, html)


def _assert_fact_pending(html: str, label: str) -> None:
    pattern = (
        re.escape(f'<th scope="row">{label}</th><td>')
        + r'<span class="sec-p1-pending-value">not emitted</span>'
    )
    assert re.search(pattern, html)


def _terminal_region(html: str) -> str:
    start = html.index('id="sec-p1-terminal-state"')
    end = html.index('<div class="sec-p1-selected-timestep"', start)
    return html[start:end]


def _selected_region(html: str) -> str:
    return html[html.index('id="sec-p1-selected-timestep"'):]


def _table_region(html: str, aria_label: str) -> str:
    match = re.search(
        rf'<table aria-label="{re.escape(aria_label)}"><tbody>(.*?)</tbody></table>',
        html,
        flags=re.DOTALL,
    )
    assert match, f"missing table {aria_label!r}"
    return match.group(1)


def _flags_region(html: str) -> str:
    start = html.index('<div class="sec-p1-flags"')
    end = html.index('<div class="note sec-p1-authority-note">', start)
    return html[start:end]


def _assert_metric_value(html: str, label: str, value: str) -> None:
    assert (
        f'<div class="k">{label}</div><div class="v">{value}</div>'
        in html
    )


def _assert_fact_value(html: str, label: str, value: str) -> None:
    assert f'<th scope="row">{label}</th><td>{value}</td>' in html


def _assert_metric_not_value(html: str, label: str, value: str) -> None:
    assert (
        f'<div class="k">{label}</div><div class="v">{value}</div>'
        not in html
    )


def _assert_fact_not_value(html: str, label: str, value: str) -> None:
    assert f'<th scope="row">{label}</th><td>{value}</td>' not in html


def _assert_chip_value(
    html: str,
    label: str,
    value: str,
    state_class: str,
) -> None:
    pattern = (
        rf'<span class="chip sec-p1-flag {re.escape(state_class)}" title="[^"]*">'
        + re.escape(f"{label} · {value}")
        + r'</span>'
    )
    assert re.search(pattern, html)


def test_p1_renders_terminal_redox_partition_breakdown_and_stage3() -> None:
    terminal_redox = _core_redox(
        source="simulator.fe_redox:<script>alert(1)</script>",
        reference="Kress & Carmichael <unsafe>",
        fe3_over_sigma_fe=0.21,
        ferric_frac=0.22,
        ferrous_frac=0.67,
        native_fe_frac=0.11,
        native_fe_partition={
            "native_fe_pool_mol": 12.5,
            "native_fe_vapor_mol": 0.5,
            "native_fe_tap_mol": 12.0,
            "native_fe_vapor_capacity_mol_hr": 0.75,
            "native_fe_vapor_capacity_kg_hr": 0.0419,
            "ordinary_melt_fe_residual_capacity_mol_hr": 1.25,
            "native_fe_vapor_escape_fraction_of_pool": 0.04,
            "native_fe_uncondensed_mol": 0.1,
            "native_fe_uncondensed_fraction_of_pool": 0.008,
            "native_fe_condensed_kg": 0.01,
            "native_fe_source_account": "process.cleaned_melt",
            "carrier_gas": "N2",
            "alpha_Fe": 0.02,
            "alpha_source": "REF-016 <script>bad()</script>",
            "alpha_s_evaluation": {
                "species": "Fe",
                "alpha_s": 0.02,
                "alpha_s_form": "scalar",
                "alpha_s_extrapolated": True,
            },
            "source_label": "pure-component Antoine",
            "series_resistance": {
                "limiting_resistance_label": "gas",
                "R_interface_fraction": 0.2,
                "R_gas_fraction": 0.7,
                "R_melt_fraction": 0.1,
                "melt_surface_renewal_source": "configured",
            },
        },
        native_fe_saturation_event={
            "native_fe_event": "native_fe_partitioned_saturation",
            "native_fe_event_reason": "native_fe_saturation_split_applied",
            "native_fe_event_status": "ok",
            "temperature_C": 1600.0,
        },
    )
    terminal_summary = {
        "fe_redox_split": terminal_redox,
        "redox_source_breakdown": {
            "net_mol_o2_equiv": 3.4,
            "delta_ln_fO2": 0.46,
            "delta_log10_fO2": 0.2,
            "redox_source_terms_applied": True,
            "redox_source_skip_reason": "not_liquid",
            "terms_mol_o2_equiv_by_label": {
                "redox_source:evaporative_metal_loss": 1.2,
                "redox_source:c3_na_shuttle_reduction": 2.2,
            },
            "applied_terms_mol_o2_equiv_by_label": {
                "redox_source:c3_na_shuttle_reduction": 2.2,
            },
            "skipped_terms_mol_o2_equiv_by_label": {
                "redox_source:evaporative_metal_loss": 1.2,
            },
            "skipped_reasons_by_label": {
                "redox_source:<script>": "blocked & retained"
            },
            "fe_redox_respeciation": {
                "respeciation_status": "ok",
                "direction": "oxidize",
                "o2_account": "process.overhead_gas",
                "oxygen_source": "overhead_gas",
            },
            "ferric_divergence": {
                "status": "warning",
                "implied_ferric_fraction": 0.2,
                "ledger_ferric_fraction": 0.18,
                "delta_abs": 0.02,
                "warning": True,
            },
            "source_context": {"campaign": "C2A", "hour": 89, "campaign_hour": 4},
            "source_campaign_hour": 4,
            "redox_source_refusal_context": {"reason": "gate <closed>"},
        },
        "stage_3_capture": {"Fe_kg": 0.42, "total_kg": 4.2, "Fe_wt_pct": 10.0},
    }
    artifact = {
        "timesteps": [
            {"hour": 88, "summary": {"fe_redox_split": _core_redox(fO2_log=-99.0)}},
            {"hour": 89, "summary": terminal_summary},
        ]
    }

    html = _render_panel(artifact)
    terminal = _terminal_region(html)
    selected = _selected_region(html)
    core_detail = _table_region(terminal, "Terminal Fe-redox state detail")
    authority = _table_region(terminal, "Fe-redox authority and validity envelope")
    partition = _table_region(terminal, "Native Fe partition detail")
    alpha_evaluation = _table_region(terminal, "Native Fe HKL alpha evaluation")
    series_resistance = _table_region(terminal, "Native Fe series-resistance model")
    saturation_event = _table_region(terminal, "Native Fe saturation event")
    redox_summary = _table_region(terminal, "Redox-source forcing summary")
    attempted_terms = _table_region(terminal, "Attempted redox-source terms by label")
    applied_terms = _table_region(terminal, "Applied redox-source terms by label")
    skipped_terms = _table_region(terminal, "Skipped redox-source terms by label")
    ferric_divergence = _table_region(terminal, "Ferric divergence")
    respeciation = _table_region(terminal, "Fe redox respeciation")
    source_context = _table_region(terminal, "Source context")
    skipped = _table_region(terminal, "Skipped reasons by source label")
    refusal = _table_region(terminal, "Refusal context")

    assert 'id="sec-p1-fe-redox"' in html
    assert "Terminal timestep · hour 89" in html
    _assert_metric_value(terminal, "Melt log₁₀ fO₂", "-8.5")
    _assert_metric_value(terminal, "Fe³⁺ / ΣFe", "0.21")
    _assert_metric_value(terminal, "Ferric fraction", "0.22")
    _assert_metric_value(terminal, "Ferrous fraction", "0.67")
    _assert_metric_value(terminal, "Native Fe fraction", "0.11")
    assert "-99" not in terminal
    _assert_fact_value(core_detail, "Fe₂O₃ / FeO molar ratio", "0.25")
    _assert_fact_value(
        core_detail,
        "IW-buffer log₁₀ fO₂ (absolute, not ΔIW)",
        "-10",
    )
    _assert_fact_pending(core_detail, "ΔIW")
    _assert_fact_value(
        core_detail,
        "Kress91 pressure input (vacuum-floored)",
        "0.001 bar",
    )
    assert "Melt-headspace pressure" not in core_detail
    _assert_fact_value(core_detail, "FeO equivalent", "7.8 wt%")
    _assert_metric_value(terminal, "Native Fe pool", "12.5 mol")
    _assert_metric_value(terminal, "Vapor route", "0.5 mol")
    _assert_metric_value(terminal, "Tap route", "12 mol")
    _assert_metric_value(terminal, "Vapor capacity", "0.75 mol/h")
    _assert_fact_value(partition, "Fe vapor mass capacity", "0.0419 kg/h")
    _assert_fact_value(
        partition,
        "Residual capacity for ordinary melt Fe",
        "1.25 mol/h",
    )
    _assert_fact_value(partition, "Condensed native Fe mass", "0.01 kg")
    _assert_fact_value(partition, "Uncondensed native Fe", "0.1 mol")
    _assert_fact_value(partition, "Native Fe pool fraction routed as vapor", "0.04")
    _assert_fact_value(partition, "Fe HKL alpha", "0.02")
    assert "Recovered product" not in partition
    assert "Collected product" not in partition
    _assert_fact_value(alpha_evaluation, "Species", "Fe")
    _assert_fact_value(alpha_evaluation, "Evaluated HKL alpha", "0.02")
    _assert_fact_value(alpha_evaluation, "HKL alpha form", "scalar")
    _assert_fact_value(alpha_evaluation, "HKL alpha extrapolated", "yes")
    _assert_fact_value(series_resistance, "Limiting resistance", "gas")
    _assert_fact_value(series_resistance, "Interface resistance fraction", "0.2")
    _assert_fact_value(series_resistance, "Gas resistance fraction", "0.7")
    _assert_fact_value(series_resistance, "Melt resistance fraction", "0.1")
    _assert_fact_value(
        saturation_event,
        "Reason",
        "native_fe_saturation_split_applied",
    )
    _assert_fact_value(
        saturation_event,
        "Event temperature",
        "1,600 °C",
    )
    _assert_fact_value(saturation_event, "Status", "ok")
    _assert_fact_value(redox_summary, "Net attempted redox-source terms", "3.4 mol O₂-eq")
    _assert_fact_value(redox_summary, "Combined skip reason", "not_liquid")
    _assert_fact_value(
        attempted_terms,
        "redox_source:evaporative_metal_loss",
        "1.2 mol O₂-eq",
    )
    _assert_fact_value(
        attempted_terms,
        "redox_source:c3_na_shuttle_reduction",
        "2.2 mol O₂-eq",
    )
    _assert_fact_value(
        applied_terms,
        "redox_source:c3_na_shuttle_reduction",
        "2.2 mol O₂-eq",
    )
    _assert_fact_not_value(
        applied_terms,
        "redox_source:evaporative_metal_loss",
        "1.2 mol O₂-eq",
    )
    _assert_fact_value(
        skipped_terms,
        "redox_source:evaporative_metal_loss",
        "1.2 mol O₂-eq",
    )
    _assert_fact_not_value(
        skipped_terms,
        "redox_source:c3_na_shuttle_reduction",
        "2.2 mol O₂-eq",
    )
    _assert_fact_value(ferric_divergence, "Implied ferric fraction", "0.2")
    _assert_fact_value(ferric_divergence, "Ledger ferric fraction", "0.18")
    _assert_fact_value(ferric_divergence, "Absolute ferric divergence", "0.02")
    _assert_fact_value(ferric_divergence, "Warning", "yes")
    assert "Ferric divergence pending" not in ferric_divergence
    _assert_fact_value(respeciation, "Respeciation status", "ok")
    _assert_fact_value(respeciation, "O2 account", "Overhead gas")
    _assert_fact_value(respeciation, "Oxygen source", "overhead_gas")
    assert "Fe redox respeciation pending" not in respeciation
    _assert_fact_value(source_context, "Campaign", "C2A")
    _assert_fact_value(source_context, "Hour", "89")
    _assert_fact_value(source_context, "Campaign hour", "4")
    assert "Source context pending" not in source_context
    _assert_metric_value(terminal, "Stage 3 Fe", "0.42 kg")
    _assert_metric_value(terminal, "Stage 3 total", "4.2 kg")
    _assert_metric_value(terminal, "Stage 3 Fe concentration", "10 wt%")
    _assert_fact_value(
        authority,
        "Source",
        "simulator.fe_redox:&lt;script&gt;alert(1)&lt;/script&gt;",
    )
    _assert_fact_value(authority, "Reference", "Kress &amp; Carmichael &lt;unsafe&gt;")
    _assert_fact_value(
        partition,
        "HKL alpha source",
        "REF-016 &lt;script&gt;bad()&lt;/script&gt;",
    )
    assert "<script>" not in html
    assert "&amp;lt;" not in html
    assert "blocked &amp; retained" in skipped
    assert "gate &lt;closed&gt;" in refusal
    assert "co-reported Stage 3 Fe capture/contamination" in html
    assert "Stage 3 Fe consequence" not in html
    _assert_metric_value(selected, "Melt log₁₀ fO₂", "-99")


def test_p1_absent_terminal_envelope_and_conditionals_stay_pending() -> None:
    html = _render_panel(_artifact({}, hour=12))
    empty_skip_html = _render_panel(
        _artifact({"redox_source_breakdown": {"redox_source_skip_reason": ""}})
    )
    missing_skip_html = _render_panel(_artifact({"redox_source_breakdown": {}}))
    terminal = _terminal_region(html)
    empty_skip_terminal = _terminal_region(empty_skip_html)
    missing_skip_terminal = _terminal_region(missing_skip_html)

    assert "Fe-redox state pending" in terminal
    assert "The terminal timestep does not emit summary.fe_redox_split" in terminal
    assert "No redox state is inferred" in terminal
    assert "Native Fe partition pending" in terminal
    assert "Native Fe saturation event pending" in terminal
    assert "Redox-source breakdown pending" in terminal
    assert "Stage 3 capture pending" in terminal
    _assert_fact_value(
        _table_region(empty_skip_terminal, "Redox-source forcing summary"),
        "Combined skip reason",
        "no skip reason",
    )
    _assert_fact_pending(
        _table_region(missing_skip_terminal, "Redox-source forcing summary"),
        "Combined skip reason",
    )


def test_p1_surfaces_explicit_and_missing_authority_flags() -> None:
    explicit = _core_redox(
        authoritative=False,
        diagnostic_only=False,
        extrapolation=True,
        high_uncertainty=False,
    )
    explicit_html = _render_panel(_artifact({"fe_redox_split": explicit}))
    missing = _core_redox()
    for key in (
        "authoritative",
        "diagnostic_only",
        "extrapolation",
        "high_uncertainty",
        "status",
        "source",
        "reference",
        "temperature_band_case",
        "temperature_band_status",
        "temperature_band_source",
    ):
        missing.pop(key)
    missing_html = _render_panel(_artifact({"fe_redox_split": missing}))
    no_iron = _core_redox(
        status="no_iron",
        source="none:no_iron",
        reference="",
        fe3_over_sigma_fe=0.0,
        ferric_frac=0.0,
        ferrous_frac=0.0,
        native_fe_frac=0.0,
    )
    no_iron_html = _render_panel(_artifact({"fe_redox_split": no_iron}))
    explicit_terminal = _terminal_region(explicit_html)
    missing_terminal = _terminal_region(missing_html)
    no_iron_terminal = _terminal_region(no_iron_html)
    explicit_flags = _flags_region(explicit_terminal)
    missing_flags = _flags_region(missing_terminal)
    no_iron_flags = _flags_region(no_iron_terminal)
    authority = _table_region(
        explicit_terminal,
        "Fe-redox authority and validity envelope",
    )
    missing_authority = _table_region(
        missing_terminal,
        "Fe-redox authority and validity envelope",
    )

    _assert_chip_value(explicit_flags, "Status", "ok", "sec-p1-flag-clear")
    _assert_chip_value(
        explicit_flags,
        "Source",
        "simulator.fe_redox:kress91_split",
        "sec-p1-flag-clear",
    )
    _assert_chip_value(
        explicit_flags,
        "Reference",
        "Kress and Carmichael 1991",
        "sec-p1-flag-clear",
    )
    _assert_chip_value(explicit_flags, "Authoritative", "no", "sec-p1-flag-caution")
    _assert_chip_value(explicit_flags, "Diagnostic only", "no", "sec-p1-flag-clear")
    _assert_chip_value(explicit_flags, "Extrapolation", "yes", "sec-p1-flag-caution")
    _assert_chip_value(explicit_flags, "High uncertainty", "no", "sec-p1-flag-clear")
    assert "diagnostic-only describes this Fe-redox split" in explicit_terminal
    assert "None is whole-run confidence" in explicit_terminal
    _assert_fact_value(authority, "Status", "ok")
    _assert_fact_value(authority, "Source", "simulator.fe_redox:kress91_split")
    _assert_fact_value(authority, "Reference", "Kress and Carmichael 1991")
    _assert_fact_value(authority, "Temperature-band case", "within_band")
    _assert_fact_value(authority, "Temperature-band status", "grounded")
    _assert_fact_value(authority, "Temperature-band source", "Kress91 basalt range")
    _assert_chip_value(missing_flags, "Status", "not emitted", "sec-p1-flag-caution")
    _assert_chip_value(missing_flags, "Source", "not emitted", "sec-p1-flag-caution")
    _assert_chip_value(missing_flags, "Reference", "not emitted", "sec-p1-flag-caution")
    _assert_chip_value(missing_flags, "Authoritative", "not emitted", "sec-p1-flag-caution")
    _assert_chip_value(missing_flags, "Diagnostic only", "not emitted", "sec-p1-flag-caution")
    _assert_chip_value(missing_flags, "Extrapolation", "not emitted", "sec-p1-flag-caution")
    _assert_chip_value(missing_flags, "High uncertainty", "not emitted", "sec-p1-flag-caution")
    _assert_fact_pending(missing_authority, "Status")
    _assert_fact_pending(missing_authority, "Source")
    _assert_fact_pending(missing_authority, "Reference")
    _assert_fact_pending(missing_authority, "Temperature-band case")
    _assert_fact_pending(missing_authority, "Temperature-band status")
    _assert_fact_pending(missing_authority, "Temperature-band source")
    # Non-ok status must be chip-visible and cautious without opening the disclosure.
    _assert_chip_value(no_iron_flags, "Status", "no_iron", "sec-p1-flag-caution")
    _assert_chip_value(no_iron_flags, "Source", "none:no_iron", "sec-p1-flag-clear")
    _assert_chip_value(no_iron_flags, "Reference", "none emitted", "sec-p1-flag-caution")
    # Chip body text (not only a title= attribute) must carry no_iron.
    assert re.search(
        r'<span class="chip sec-p1-flag [^"]*"[^>]*>Status · no_iron</span>',
        no_iron_flags,
    )

    flag_specs = {
        "authoritative": ("Authoritative", "sec-p1-flag-clear"),
        "diagnostic_only": ("Diagnostic only", "sec-p1-flag-caution"),
        "extrapolation": ("Extrapolation", "sec-p1-flag-caution"),
        "high_uncertainty": ("High uncertainty", "sec-p1-flag-caution"),
    }
    for enabled_key, _ in flag_specs.items():
        one_hot = _core_redox(
            authoritative=False,
            diagnostic_only=False,
            extrapolation=False,
            high_uncertainty=False,
        )
        one_hot[enabled_key] = True
        one_hot_flags = _flags_region(
            _terminal_region(_render_panel(_artifact({"fe_redox_split": one_hot})))
        )
        for key, (label, true_class) in flag_specs.items():
            expected_value = "yes" if key == enabled_key else "no"
            if key == enabled_key:
                expected_class = true_class
            else:
                expected_class = (
                    "sec-p1-flag-caution"
                    if key == "authoritative"
                    else "sec-p1-flag-clear"
                )
            _assert_chip_value(
                one_hot_flags,
                label,
                expected_value,
                expected_class,
            )


def test_p1_null_empty_and_malformed_authority_metadata_are_distinct() -> None:
    malformed_redox = _core_redox(status=None, source=[], reference={})
    malformed_terminal = _terminal_region(
        _render_panel(_artifact({"fe_redox_split": malformed_redox}))
    )
    malformed_flags = _flags_region(malformed_terminal)
    malformed_authority = _table_region(
        malformed_terminal,
        "Fe-redox authority and validity envelope",
    )
    malformed_tooltip = (
        'title="status: not emitted; source: malformed; reference: malformed"'
    )

    assert malformed_flags.count(malformed_tooltip) == 7
    _assert_chip_value(malformed_flags, "Status", "not emitted", "sec-p1-flag-caution")
    _assert_chip_value(malformed_flags, "Source", "malformed", "sec-p1-flag-caution")
    _assert_chip_value(malformed_flags, "Reference", "malformed", "sec-p1-flag-caution")
    _assert_fact_pending(malformed_authority, "Status")
    _assert_fact_value(malformed_authority, "Source", "malformed")
    _assert_fact_value(malformed_authority, "Reference", "malformed")
    assert "null" not in malformed_flags
    assert "[object Object]" not in malformed_flags

    empty_redox = _core_redox(status="", source="", reference="")
    empty_terminal = _terminal_region(
        _render_panel(_artifact({"fe_redox_split": empty_redox}))
    )
    empty_flags = _flags_region(empty_terminal)
    empty_authority = _table_region(
        empty_terminal,
        "Fe-redox authority and validity envelope",
    )
    empty_tooltip = 'title="status: none emitted; source: none emitted; reference: none emitted"'

    assert empty_flags.count(empty_tooltip) == 7
    _assert_chip_value(empty_flags, "Status", "none emitted", "sec-p1-flag-caution")
    _assert_chip_value(empty_flags, "Source", "none emitted", "sec-p1-flag-caution")
    _assert_chip_value(empty_flags, "Reference", "none emitted", "sec-p1-flag-caution")
    _assert_fact_value(empty_authority, "Status", "none emitted")
    _assert_fact_value(empty_authority, "Source", "none emitted")
    _assert_fact_value(empty_authority, "Reference", "none emitted")

    zero_redox = _core_redox(status=0, source=False, reference=0)
    zero_terminal = _terminal_region(
        _render_panel(_artifact({"fe_redox_split": zero_redox}))
    )
    zero_flags = _flags_region(zero_terminal)
    assert zero_flags.count('title="status: 0; source: false; reference: 0"') == 7
    _assert_chip_value(zero_flags, "Status", "0", "sec-p1-flag-caution")
    _assert_chip_value(zero_flags, "Source", "false", "sec-p1-flag-clear")
    _assert_chip_value(zero_flags, "Reference", "0", "sec-p1-flag-clear")


def test_p1_timestep_hour_states_distinguish_absent_empty_malformed_and_zero() -> None:
    cases = [
        ({"summary": {}}, "not emitted"),
        ({"hour": None, "summary": {}}, "not emitted"),
        ({"hour": "", "summary": {}}, "empty"),
        ({"hour": {}, "summary": {}}, "malformed"),
        ({"hour": [], "summary": {}}, "malformed"),
        ({"hour": False, "summary": {}}, "malformed"),
        ({"hour": "88", "summary": {}}, "malformed"),
        ({"hour": 0, "summary": {}}, "0"),
    ]

    for timestep, expected in cases:
        html = _render_panel({"timesteps": [timestep]})
        assert f"Terminal timestep · hour {expected}." in html
        assert f">hour {expected}</span>" in _selected_region(html)


def test_p1_selected_timestep_view_tracks_inspector_index() -> None:
    redox_88 = _core_redox(
        fO2_log=-99.0,
        status="no_iron",
        source="none:no_iron",
        authoritative=False,
        native_fe_partition={
            "native_fe_pool_mol": 12.0,
            "native_fe_vapor_mol": 3.0,
            "native_fe_tap_mol": 9.0,
        },
        native_fe_saturation_event={
            "native_fe_event": "native_fe_partition_refused",
            "native_fe_event_reason": "partition_refused",
            "native_fe_event_status": "refused",
            "temperature_C": 1200.0,
        },
    )
    redox_89 = _core_redox(
        fO2_log=-8.5,
        native_fe_partition={
            "native_fe_pool_mol": 22.0,
            "native_fe_vapor_mol": 4.0,
            "native_fe_tap_mol": 18.0,
        },
        native_fe_saturation_event={
            "native_fe_event": "native_fe_partitioned_saturation",
            "native_fe_event_reason": "partition_applied",
            "native_fe_event_status": "ok",
            "temperature_C": 1600.0,
        },
    )
    artifact = {
        "timesteps": [
            {
                "hour": 88,
                "summary": {
                    "fe_redox_split": redox_88,
                    "redox_source_breakdown": {
                        "net_mol_o2_equiv": 4.0,
                        "redox_source_skip_reason": "no_melt_redox_capacity",
                        "terms_mol_o2_equiv_by_label": {"redox_source:attempted_88": 4.0},
                        "applied_terms_mol_o2_equiv_by_label": {},
                        "skipped_terms_mol_o2_equiv_by_label": {
                            "redox_source:attempted_88": 4.0
                        },
                        "redox_source_refusal_context": {"reason": "capacity_refusal"},
                    },
                    "stage_3_capture": {"Fe_kg": 5.0, "total_kg": 10.0, "Fe_wt_pct": 50.0},
                },
            },
            {
                "hour": 89,
                "summary": {
                    "fe_redox_split": redox_89,
                    "redox_source_breakdown": {
                        "net_mol_o2_equiv": 7.0,
                        "redox_source_skip_reason": "not_liquid",
                        "terms_mol_o2_equiv_by_label": {"redox_source:attempted_89": 7.0},
                        "applied_terms_mol_o2_equiv_by_label": {
                            "redox_source:attempted_89": 7.0
                        },
                        "skipped_terms_mol_o2_equiv_by_label": {},
                        "redox_source_refusal_context": {"reason": "liquid_gate_refusal"},
                    },
                    "stage_3_capture": {"Fe_kg": 1.0, "total_kg": 20.0, "Fe_wt_pct": 5.0},
                },
            },
        ]
    }

    initial = _selected_region(_render_panel(artifact))
    selected_88 = _render_panel_timestep(artifact, 0)
    selected_89 = _render_panel_timestep(artifact, 1)

    assert ">hour 88</span>" in initial
    _assert_metric_value(initial, "Melt log₁₀ fO₂", "-99")
    assert ">hour 88</span>" in selected_88
    _assert_metric_value(selected_88, "Melt log₁₀ fO₂", "-99")
    _assert_chip_value(
        _flags_region(selected_88),
        "Status",
        "no_iron",
        "sec-p1-flag-caution",
    )
    _assert_chip_value(
        _flags_region(selected_88),
        "Authoritative",
        "no",
        "sec-p1-flag-caution",
    )
    _assert_metric_value(selected_88, "Native Fe pool", "12 mol")
    _assert_fact_value(
        _table_region(selected_88, "Native Fe saturation event"),
        "Status",
        "refused",
    )
    _assert_fact_value(
        _table_region(selected_88, "Native Fe saturation event"),
        "Reason",
        "partition_refused",
    )
    _assert_fact_value(
        _table_region(selected_88, "Redox-source forcing summary"),
        "Net attempted redox-source terms",
        "4 mol O₂-eq",
    )
    _assert_fact_value(
        _table_region(selected_88, "Redox-source forcing summary"),
        "Combined skip reason",
        "no_melt_redox_capacity",
    )
    _assert_fact_value(
        _table_region(selected_88, "Skipped redox-source terms by label"),
        "redox_source:attempted_88",
        "4 mol O₂-eq",
    )
    _assert_fact_value(
        _table_region(selected_88, "Refusal context"),
        "Reason",
        "capacity_refusal",
    )
    _assert_metric_value(selected_88, "Stage 3 Fe", "5 kg")
    _assert_metric_value(selected_88, "Stage 3 total", "10 kg")
    _assert_metric_value(selected_88, "Stage 3 Fe concentration", "50 wt%")
    assert "22 mol" not in selected_88
    assert "liquid_gate_refusal" not in selected_88
    assert ">hour 89</span>" in selected_89
    _assert_metric_value(selected_89, "Melt log₁₀ fO₂", "-8.5")
    _assert_chip_value(_flags_region(selected_89), "Status", "ok", "sec-p1-flag-clear")
    _assert_metric_value(selected_89, "Native Fe pool", "22 mol")
    _assert_fact_value(
        _table_region(selected_89, "Native Fe saturation event"),
        "Reason",
        "partition_applied",
    )
    _assert_fact_value(
        _table_region(selected_89, "Redox-source forcing summary"),
        "Net attempted redox-source terms",
        "7 mol O₂-eq",
    )
    _assert_fact_value(
        _table_region(selected_89, "Refusal context"),
        "Reason",
        "liquid_gate_refusal",
    )
    _assert_metric_value(selected_89, "Stage 3 Fe", "1 kg")
    _assert_metric_value(selected_89, "Stage 3 Fe concentration", "5 wt%")
    assert "12 mol" not in selected_89
    assert "capacity_refusal" not in selected_89


def test_p1_malformed_artifact_shapes_render_panel_local_pending() -> None:
    malformed_artifacts = [
        None,
        {},
        {"timesteps": None},
        {"timesteps": "not-an-array"},
        {"timesteps": []},
        {"timesteps": [None]},
        {"timesteps": [{}]},
        {"timesteps": [{"hour": 1}]},
        {"timesteps": [{"hour": 1, "summary": []}]},
        {
            "timesteps": [
                {
                    "hour": 1,
                    "summary": {
                        "fe_redox_split": [],
                        "redox_source_breakdown": "malformed",
                        "stage_3_capture": [],
                    },
                }
            ]
        },
    ]

    for artifact in malformed_artifacts:
        html = _render_panel(artifact)
        terminal = _terminal_region(html)
        assert 'id="sec-p1-fe-redox"' in html
        assert "Fe-redox state pending" in terminal
        assert "Native Fe partition pending" in terminal
        assert "Redox-source breakdown pending" in terminal
        assert "Stage 3 capture pending" in terminal


def test_p1_on_timestep_handles_malformed_artifacts_and_indices() -> None:
    malformed_artifacts = [
        None,
        {},
        {"timesteps": None},
        {"timesteps": "not-an-array"},
        {"timesteps": []},
        {"timesteps": [None]},
        {"timesteps": [{}]},
        {"timesteps": [{"hour": 1}]},
        {"timesteps": [{"hour": 1, "summary": []}]},
        {
            "timesteps": [
                {
                    "hour": 1,
                    "summary": {
                        "fe_redox_split": [],
                        "redox_source_breakdown": "malformed",
                        "stage_3_capture": [],
                    },
                }
            ]
        },
    ]

    for artifact in malformed_artifacts:
        selected = _render_panel_timestep(artifact, 0)
        assert "Selected timestep Fe-redox" in selected
        assert "Fe-redox state pending" in selected
        assert "Native Fe partition pending" in selected
        assert (
            "summary.fe_redox_split.native_fe_partition is conditional and was not "
            "emitted for the selected timestep"
        ) in selected
        assert "Native Fe saturation event pending" in selected
        assert (
            "summary.fe_redox_split.native_fe_saturation_event was not emitted for "
            "the selected timestep"
        ) in selected
        assert "Redox-source breakdown pending" in selected
        assert (
            "The selected timestep does not emit summary.redox_source_breakdown"
        ) in selected
        assert "Stage 3 capture pending" in selected
        assert "The selected timestep does not emit summary.stage_3_capture" in selected
        assert "terminal timestep" not in selected

    valid_artifact = _artifact({"fe_redox_split": _core_redox()}, hour=4)
    for index in (-1, 1, 99):
        selected = _render_panel_timestep(valid_artifact, index)
        assert ">hour not emitted</span>" in selected
        assert "Fe-redox state pending" in selected


def test_p1_partition_and_event_absent_before_hour_89_are_not_zeroed() -> None:
    html = _render_panel(
        _artifact(
            {
                "fe_redox_split": _core_redox(),
                "redox_source_breakdown": {},
                "stage_3_capture": {"Fe_kg": 0.0, "total_kg": 0.0, "Fe_wt_pct": 0.0},
            },
            hour=88,
        )
    )
    terminal = _terminal_region(html)

    assert "Terminal timestep · hour 88" in html
    assert "Native Fe partition pending" in terminal
    assert "No empty or zero partition is assumed" in terminal
    assert "Native Fe saturation event pending" in terminal
    assert "Native Fe pool</div>" not in terminal
    _assert_metric_value(terminal, "Stage 3 Fe", "0 kg")


def test_p1_absent_core_numeric_fields_never_default_or_derive() -> None:
    specs = [
        ("fO2_log", "Melt log₁₀ fO₂", "metric", "0"),
        ("fe3_over_sigma_fe", "Fe³⁺ / ΣFe", "metric", "0"),
        ("ferric_frac", "Ferric fraction", "metric", "0"),
        ("ferrous_frac", "Ferrous fraction", "metric", "0"),
        ("native_fe_frac", "Native Fe fraction", "metric", "0"),
        ("iw_log", "IW-buffer log₁₀ fO₂ (absolute, not ΔIW)", "fact", "0"),
        ("temperature_K", "Temperature", "fact", "0 K"),
        (
            "pressure_bar",
            "Kress91 pressure input (vacuum-floored)",
            "fact",
            "0 bar",
        ),
        ("fe2o3_over_feo_molar", "Fe₂O₃ / FeO molar ratio", "fact", "0"),
        ("fe2o3_equiv_wt_pct", "Fe₂O₃ equivalent", "fact", "0 wt%"),
        ("feo_equiv_wt_pct", "FeO equivalent", "fact", "0 wt%"),
    ]

    for key, label, region_kind, forbidden_zero in specs:
        redox = _core_redox()
        redox.pop(key)
        terminal = _terminal_region(
            _render_panel(
                _artifact(
                    {
                        "T_C": 1726.85,
                        "P_total_bar": 0.25,
                        "pO2_bar": 1e-8,
                        "fe_redox_split": redox,
                    }
                )
            )
        )
        region = (
            terminal
            if region_kind == "metric"
            else _table_region(terminal, "Terminal Fe-redox state detail")
        )
        if region_kind == "metric":
            _assert_metric_pending(region, label)
            _assert_metric_not_value(region, label, forbidden_zero)
        else:
            _assert_fact_pending(region, label)
            _assert_fact_not_value(region, label, forbidden_zero)

        if key == "fO2_log":
            _assert_metric_not_value(region, label, "-8")
        elif key == "temperature_K":
            _assert_fact_not_value(region, label, "2,000 K")
        elif key == "pressure_bar":
            _assert_fact_not_value(region, label, "0.25 bar")


def test_p1_redox_term_maps_distinguish_absent_empty_malformed_and_zero() -> None:
    titles = (
        "Attempted redox-source terms by label",
        "Applied redox-source terms by label",
        "Skipped redox-source terms by label",
    )
    absent = _terminal_region(
        _render_panel(
            _artifact({"redox_source_breakdown": {"net_mol_o2_equiv": 6.0}})
        )
    )
    empty = _terminal_region(
        _render_panel(
            _artifact(
                {
                    "redox_source_breakdown": {
                        "terms_mol_o2_equiv_by_label": {},
                        "applied_terms_mol_o2_equiv_by_label": {},
                        "skipped_terms_mol_o2_equiv_by_label": {},
                    }
                }
            )
        )
    )
    malformed = _terminal_region(
        _render_panel(
            _artifact(
                {
                    "redox_source_breakdown": {
                        "terms_mol_o2_equiv_by_label": [],
                        "applied_terms_mol_o2_equiv_by_label": "bad",
                        "skipped_terms_mol_o2_equiv_by_label": 4,
                    }
                }
            )
        )
    )
    zero = _terminal_region(
        _render_panel(
            _artifact(
                {
                    "redox_source_breakdown": {
                        "terms_mol_o2_equiv_by_label": {"redox_source:zero": 0.0},
                        "applied_terms_mol_o2_equiv_by_label": {},
                        "skipped_terms_mol_o2_equiv_by_label": {},
                    }
                }
            )
        )
    )

    for title in titles:
        assert f"{title} pending" in absent
        assert f"{title} malformed" in malformed
        assert f"{title} pending" not in empty
    assert empty.count("Emitted object contains no terms.") == 3
    assert malformed.count("was emitted with a malformed non-object value") == 3
    _assert_fact_value(
        _table_region(zero, "Attempted redox-source terms by label"),
        "redox_source:zero",
        "0 mol O₂-eq",
    )


def test_p1_partial_inputs_never_drive_viewer_derivations() -> None:
    derived_log10 = 3.4567
    partial_redox = _core_redox(
        fO2_log=-8.0,
        iw_log=-10.5,
        ferric_frac=0.1234,
        native_fe_frac=0.2345,
        native_fe_partition={
            "native_fe_pool_mol": 11.0,
            "native_fe_vapor_mol": 2.5,
            "native_fe_uncondensed_mol": 1.65,
            "native_fe_vapor_capacity_mol_hr": 4.0,
        },
    )
    partial_redox.pop("ferrous_frac")
    partial_redox.pop("fe3_over_sigma_fe")
    html = _render_panel(
        _artifact(
            {
                "fe_redox_split": partial_redox,
                "redox_source_breakdown": {
                    "terms_mol_o2_equiv_by_label": {
                        "term_a": 1.111,
                        "term_b": 2.222,
                    },
                    "delta_ln_fO2": derived_log10 * 2.302585092994046,
                    "redox_source_terms_applied": True,
                    "skipped_reasons_by_label": {},
                    "fe_redox_respeciation": {},
                    "ferric_divergence": {},
                    "source_context": {},
                    "redox_source_refusal_context": {},
                },
                "stage_3_capture": {"Fe_kg": 2.0, "total_kg": 8.0},
            }
        )
    )
    terminal = _terminal_region(html)
    core_detail = _table_region(terminal, "Terminal Fe-redox state detail")
    partition = _table_region(terminal, "Native Fe partition detail")
    redox_summary = _table_region(terminal, "Redox-source forcing summary")
    attempted_terms = _table_region(terminal, "Attempted redox-source terms by label")
    missing_ferric = _core_redox(fe3_over_sigma_fe=0.3456)
    missing_ferric.pop("ferric_frac")
    missing_ferric_terminal = _terminal_region(
        _render_panel(_artifact({"fe_redox_split": missing_ferric}))
    )
    missing_native = _core_redox(ferric_frac=0.2468, ferrous_frac=0.5)
    missing_native.pop("native_fe_frac")
    missing_native_terminal = _terminal_region(
        _render_panel(_artifact({"fe_redox_split": missing_native}))
    )
    # Codex partial tuple: ingredients present, emitted fraction absent → never 0.2.
    uncondensed_partial = _core_redox(
        native_fe_partition={
            "native_fe_pool_mol": 10.0,
            "native_fe_uncondensed_mol": 2.0,
        }
    )
    uncondensed_terminal = _terminal_region(
        _render_panel(_artifact({"fe_redox_split": uncondensed_partial}))
    )
    uncondensed_partition = _table_region(
        uncondensed_terminal, "Native Fe partition detail"
    )

    def partition_region_for(partition_fields: dict) -> str:
        redox = _core_redox(native_fe_partition=partition_fields)
        rendered = _terminal_region(
            _render_panel(_artifact({"fe_redox_split": redox}))
        )
        return _table_region(rendered, "Native Fe partition detail")

    pool_missing_terminal = _terminal_region(
        _render_panel(
            _artifact(
                {
                    "fe_redox_split": _core_redox(
                        native_fe_partition={
                            "native_fe_vapor_mol": 2.25,
                            "native_fe_tap_mol": 7.75,
                        }
                    )
                }
            )
        )
    )
    vapor_missing_terminal = _terminal_region(
        _render_panel(
            _artifact(
                {
                    "fe_redox_split": _core_redox(
                        native_fe_partition={
                            "native_fe_pool_mol": 10.0,
                            "native_fe_tap_mol": 7.75,
                        }
                    )
                }
            )
        )
    )
    mol_capacity_missing_terminal = _terminal_region(
        _render_panel(
            _artifact(
                {
                    "fe_redox_split": _core_redox(
                        native_fe_partition={
                            "native_fe_vapor_capacity_kg_hr": 0.22338,
                        }
                    )
                }
            )
        )
    )
    mol_capacity_from_residual_missing_terminal = _terminal_region(
        _render_panel(
            _artifact(
                {
                    "fe_redox_split": _core_redox(
                        native_fe_partition={
                            "native_fe_vapor_mol": 2.25,
                            "ordinary_melt_fe_residual_capacity_mol_hr": 1.75,
                        }
                    )
                }
            )
        )
    )
    vapor_from_fraction_missing_terminal = _terminal_region(
        _render_panel(
            _artifact(
                {
                    "fe_redox_split": _core_redox(
                        native_fe_partition={
                            "native_fe_pool_mol": 10.0,
                            "native_fe_vapor_escape_fraction_of_pool": 0.225,
                        }
                    )
                }
            )
        )
    )
    uncondensed_mol_missing_partition = partition_region_for(
        {
            "native_fe_pool_mol": 10.0,
            "native_fe_uncondensed_fraction_of_pool": 0.2,
        }
    )
    reverse_ferric = _core_redox(ferrous_frac=0.5432, native_fe_frac=0.3211)
    reverse_ferric.pop("ferric_frac")
    reverse_ferric_terminal = _terminal_region(
        _render_panel(_artifact({"fe_redox_split": reverse_ferric}))
    )
    stage_fe_missing_terminal = _terminal_region(
        _render_panel(
            _artifact({"stage_3_capture": {"total_kg": 8.0, "Fe_wt_pct": 25.0}})
        )
    )
    stage_total_missing_terminal = _terminal_region(
        _render_panel(
            _artifact({"stage_3_capture": {"Fe_kg": 2.0, "Fe_wt_pct": 25.0}})
        )
    )
    delta_ln_missing_summary = _table_region(
        _terminal_region(
            _render_panel(
                _artifact({"redox_source_breakdown": {"delta_log10_fO2": 2.0}})
            )
        ),
        "Redox-source forcing summary",
    )

    _assert_metric_pending(terminal, "Fe³⁺ / ΣFe")
    _assert_metric_pending(terminal, "Ferrous fraction")
    _assert_metric_pending(terminal, "Tap route")
    _assert_metric_pending(terminal, "Stage 3 Fe concentration")
    _assert_fact_pending(partition, "Fe vapor mass capacity")
    _assert_fact_pending(partition, "Residual capacity for ordinary melt Fe")
    _assert_fact_pending(partition, "Condensed native Fe mass")
    _assert_fact_pending(partition, "Uncondensed fraction of native Fe pool")
    _assert_fact_pending(partition, "Native Fe pool fraction routed as vapor")
    _assert_fact_pending(redox_summary, "Net attempted redox-source terms")
    _assert_fact_pending(redox_summary, "Change in log₁₀ fO₂")
    _assert_fact_value(attempted_terms, "term_a", "1.111 mol O₂-eq")
    _assert_fact_value(attempted_terms, "term_b", "2.222 mol O₂-eq")
    assert "Applied redox-source terms by label pending" in terminal
    assert "Skipped redox-source terms by label pending" in terminal
    _assert_fact_not_value(attempted_terms, "term_a", "3.333 mol O₂-eq")
    _assert_metric_pending(missing_ferric_terminal, "Ferric fraction")
    _assert_metric_not_value(missing_ferric_terminal, "Ferric fraction", "0.3456")
    _assert_metric_pending(missing_native_terminal, "Native Fe fraction")
    _assert_metric_not_value(missing_native_terminal, "Native Fe fraction", "0.2532")
    _assert_fact_pending(uncondensed_partition, "Uncondensed fraction of native Fe pool")
    _assert_fact_not_value(
        uncondensed_partition,
        "Uncondensed fraction of native Fe pool",
        "0.2",
    )
    _assert_metric_pending(pool_missing_terminal, "Native Fe pool")
    _assert_metric_not_value(pool_missing_terminal, "Native Fe pool", "10 mol")
    _assert_metric_pending(vapor_missing_terminal, "Vapor route")
    _assert_metric_not_value(vapor_missing_terminal, "Vapor route", "2.25 mol")
    _assert_metric_pending(mol_capacity_missing_terminal, "Vapor capacity")
    _assert_metric_not_value(
        mol_capacity_missing_terminal,
        "Vapor capacity",
        "4 mol/h",
    )
    _assert_metric_pending(
        mol_capacity_from_residual_missing_terminal,
        "Vapor capacity",
    )
    _assert_metric_not_value(
        mol_capacity_from_residual_missing_terminal,
        "Vapor capacity",
        "4 mol/h",
    )
    _assert_metric_pending(vapor_from_fraction_missing_terminal, "Vapor route")
    _assert_metric_not_value(
        vapor_from_fraction_missing_terminal,
        "Vapor route",
        "2.25 mol",
    )
    _assert_fact_pending(uncondensed_mol_missing_partition, "Uncondensed native Fe")
    _assert_fact_not_value(
        uncondensed_mol_missing_partition,
        "Uncondensed native Fe",
        "2 mol",
    )
    _assert_metric_pending(reverse_ferric_terminal, "Ferric fraction")
    _assert_metric_not_value(reverse_ferric_terminal, "Ferric fraction", "0.1357")
    _assert_metric_pending(stage_fe_missing_terminal, "Stage 3 Fe")
    _assert_metric_not_value(stage_fe_missing_terminal, "Stage 3 Fe", "2 kg")
    _assert_metric_pending(stage_total_missing_terminal, "Stage 3 total")
    _assert_metric_not_value(stage_total_missing_terminal, "Stage 3 total", "8 kg")
    _assert_fact_pending(delta_ln_missing_summary, "Change in ln fO₂")
    _assert_fact_not_value(delta_ln_missing_summary, "Change in ln fO₂", "4.605")
    _assert_fact_value(
        core_detail,
        "IW-buffer log₁₀ fO₂ (absolute, not ΔIW)",
        "-10.5",
    )
    _assert_fact_pending(core_detail, "ΔIW")
    _assert_fact_not_value(core_detail, "ΔIW", "2.5")
    _assert_metric_not_value(terminal, "Fe³⁺ / ΣFe", "0.1234")
    _assert_metric_not_value(terminal, "Ferrous fraction", "0.6421")
    _assert_metric_not_value(terminal, "Tap route", "8.5 mol")
    _assert_fact_not_value(
        partition,
        "Native Fe pool fraction routed as vapor",
        "0.2273",
    )
    _assert_fact_not_value(
        partition,
        "Uncondensed fraction of native Fe pool",
        "0.15",
    )
    _assert_fact_not_value(partition, "Fe vapor mass capacity", "0.2234 kg/h")
    _assert_fact_not_value(
        partition,
        "Residual capacity for ordinary melt Fe",
        "1.5 mol/h",
    )
    _assert_fact_not_value(partition, "Condensed native Fe mass", "0.1396 kg")
    _assert_fact_not_value(partition, "Condensed native Fe mass", "0.04747 kg")
    _assert_fact_not_value(
        redox_summary,
        "Net attempted redox-source terms",
        "3.333 mol O₂-eq",
    )
    _assert_fact_not_value(redox_summary, "Change in log₁₀ fO₂", "3.457")
    _assert_metric_not_value(terminal, "Stage 3 Fe concentration", "25 wt%")
