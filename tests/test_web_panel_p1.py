import json
import re
import subprocess
from pathlib import Path


REPORT_ROOT = Path(__file__).resolve().parents[1] / "web" / "report_viewer"


def _render_panel(artifact: dict) -> str:
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


def test_p1_renders_terminal_redox_partition_breakdown_and_stage3() -> None:
    terminal_redox = _core_redox(
        source="simulator.fe_redox:<script>alert(1)</script>",
        reference="Kress & Carmichael <unsafe>",
        native_fe_partition={
            "native_fe_pool_mol": 12.5,
            "native_fe_vapor_mol": 0.5,
            "native_fe_tap_mol": 12.0,
            "native_fe_vapor_capacity_mol_hr": 0.75,
            "native_fe_vapor_capacity_kg_hr": 0.0419,
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
                "alpha_s_extrapolated": False,
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
            "skipped_reasons_by_label": {
                "redox_source:<script>": "blocked & retained"
            },
            "fe_redox_respeciation": {
                "respeciation_status": "ok",
                "direction": "oxidize",
                "o2_account": "process.overhead_gas",
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

    assert 'id="sec-p1-fe-redox"' in html
    assert "Terminal timestep · hour 89" in html
    assert "Melt log₁₀ fO₂" in html
    assert "-8.5" in html
    assert "-99" not in html
    assert "Fe₂O₃ / FeO molar ratio" in html
    assert "12.5 mol" in html
    assert "12 mol" in html
    assert "Condensed native Fe mass" in html
    assert "Native Fe pool fraction routed as vapor" in html
    assert "Fe HKL alpha" in html
    assert "Evaluated HKL alpha" in html
    assert "Limiting resistance" in html
    assert "native_fe_saturation_split_applied" in html
    assert "1,600 °C" in html
    assert "Net attempted redox-source terms" in html
    assert "3.4 mol O₂-eq" in html
    assert "Ferric divergence" in html
    assert "0.42 kg" in html
    assert "4.2 kg" in html
    assert "10 wt%" in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "blocked &amp; retained" in html
    assert "gate &lt;closed&gt;" in html


def test_p1_absent_terminal_envelope_and_conditionals_stay_pending() -> None:
    html = _render_panel(_artifact({}, hour=12))
    empty_skip_html = _render_panel(
        _artifact({"redox_source_breakdown": {"redox_source_skip_reason": ""}})
    )
    missing_skip_html = _render_panel(_artifact({"redox_source_breakdown": {}}))

    assert "Fe-redox state pending" in html
    assert "No redox state is inferred" in html
    assert "Native Fe partition pending" in html
    assert "Native Fe saturation event pending" in html
    assert "Redox-source breakdown pending" in html
    assert "Stage 3 capture pending" in html
    assert '<th scope="row">Combined skip reason</th><td>no skip reason</td>' in empty_skip_html
    _assert_fact_pending(missing_skip_html, "Combined skip reason")


def test_p1_surfaces_explicit_and_missing_authority_flags() -> None:
    explicit = _core_redox(
        authoritative=False,
        diagnostic_only=True,
        extrapolation=True,
        high_uncertainty=True,
    )
    explicit_html = _render_panel(_artifact({"fe_redox_split": explicit}))
    missing = _core_redox()
    for key in ("authoritative", "diagnostic_only", "extrapolation", "high_uncertainty"):
        missing.pop(key)
    missing_html = _render_panel(_artifact({"fe_redox_split": missing}))

    assert "Authoritative · no" in explicit_html
    assert "Diagnostic only · yes" in explicit_html
    assert "Extrapolation · yes" in explicit_html
    assert "High uncertainty · yes" in explicit_html
    assert "diagnostic-only describes this Fe-redox split" in explicit_html
    assert "None is whole-run confidence" in explicit_html
    assert "Authoritative · not emitted" in missing_html
    assert "Diagnostic only · not emitted" in missing_html
    assert "Extrapolation · not emitted" in missing_html
    assert "High uncertainty · not emitted" in missing_html


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

    assert "Terminal timestep · hour 88" in html
    assert "Native Fe partition pending" in html
    assert "No empty or zero partition is assumed" in html
    assert "Native Fe saturation event pending" in html
    assert "Native Fe pool</div>" not in html
    assert "Stage 3 Fe</div><div class=\"v\">0 kg" in html


def test_p1_partial_inputs_never_drive_viewer_derivations() -> None:
    derived_log10 = 3.4567
    partial_redox = _core_redox(
        fO2_log=-8.0,
        iw_log=-10.5,
        ferric_frac=0.1234,
        native_fe_frac=0.2345,
        native_fe_partition={
            "native_fe_pool_mol": 14.9,
            "native_fe_vapor_mol": 2.2,
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

    _assert_metric_pending(html, "Fe³⁺ / ΣFe")
    _assert_metric_pending(html, "Ferrous fraction")
    _assert_metric_pending(html, "Tap route")
    _assert_metric_pending(html, "Stage 3 Fe concentration")
    _assert_fact_pending(html, "Native Fe pool fraction routed as vapor")
    _assert_fact_pending(html, "Net attempted redox-source terms")
    _assert_fact_pending(html, "Change in log₁₀ fO₂")
    assert "IW-buffer log₁₀ fO₂ (absolute, not ΔIW)" in html
    assert ">2.5<" not in html
    assert "0.6421" not in html
    assert "12.7 mol" not in html
    assert "0.1477" not in html
    assert "3.333" not in html
    assert "3.457" not in html
    assert "25 wt%" not in html
