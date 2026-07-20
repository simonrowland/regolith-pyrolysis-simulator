from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "web/report_viewer/panels/p8-cost-rollup.js"
LABELS = ROOT / "web/report_viewer/labels.js"


def _render_panel(artifact: dict, *, energy: dict | None = None) -> dict[str, str]:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const panel = context.ReportPanels[0];
const html = panel.render(JSON.parse(process.argv[4]), [], [], JSON.parse(process.argv[5]));
process.stdout.write(JSON.stringify({ id: panel.id, html }));
"""
    completed = subprocess.run(
        ["node", "-", str(LABELS), str(PANEL), json.dumps(artifact), json.dumps(energy or {})],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _full_artifact() -> dict:
    return {
        "header": {
            "cost_block": {
                "electrical_cost_per_kWh": 10.0,
                "solar_heat_cost_per_kWh": 0.05,
                "provenance": "owner <source>",
            }
        },
        "terminal": {
            "cost_totals": {
                "process_electrical_energy_kWh": 2.0,
                "process_electrical_cost_usd": 20.0,
                "pumping_electrical_energy_kWh": 3.0,
                "pumping_electrical_cost_usd": 30.0,
                "electrical_energy_kWh": 5.0,
                "electrical_cost_usd": 50.0,
                "evaporation_thermal_energy_kWh": 40.0,
                "solar_heat_cost_usd": 2.0,
                "total_cost_usd": 52.0,
                "basis_note": "pumping included after resolved status",
            },
            "run_metadata": {
                "cost_rollup_diagnostic": {
                    "schema_version": "cost-ledger-v1",
                    "policy_id": "policy <unsafe>",
                    "price_basis": "legacy_placeholder_awaiting_owner_ratification",
                    "transition_count": 7,
                    "transition_balance_max_abs": 1e-13,
                    "product_costs": {
                        "process.cleaned_melt:SiO2": {
                            "quantity_kg": 4.0,
                            "accumulated_cost": {
                                "electrical_kWh": 1.0,
                                "thermal_flux_h": 2.0,
                                "furnace_h": 3.0,
                                "launch_penalty_kg": 4.0,
                                "external_reagent_kg": 5.0,
                            },
                            "owner_ratify_money_projection": 50631.0,
                        }
                    },
                    "active_inventory_costs": {
                        "process.metal_phase_bottom_pool:Fe": {
                            "electrical_kWh": 6.0,
                            "thermal_flux_h": 7.0,
                            "furnace_h": 8.0,
                            "launch_penalty_kg": 9.0,
                            "external_reagent_kg": 10.0,
                        }
                    },
                    "run_input_cost": {
                        "thermal_proxy": "thermal_flux_h = absolute_temperature_K * duration_h",
                        "physical_cost": {
                            "electrical_kWh": 11.0,
                            "thermal_flux_h": 12.0,
                            "furnace_h": 13.0,
                            "launch_penalty_kg": 14.0,
                            "external_reagent_kg": 15.0,
                        },
                        "allocation_status": "allocated_by_product_mass",
                        "owner_ratify_money_projection": 141642.0,
                    },
                    "auxiliary_electrical_diagnostic": {
                        "schema_version": "auxiliary-electrical-rollup-v1",
                        "components_kWh": {"condenser": 0.25, "pumping": 3.0, "turbine": 0.01},
                        "auxiliary_electrical_kWh": 3.26,
                    },
                    "pumping_diagnostic": {
                        "schema_version": "pumping-cost-rollup-v1",
                        "status": "resolved",
                        "pumping_electrical_kWh": 3.0,
                        "feasible": True,
                        "parameter_metadata": [{
                            "name": "pump_stage_isentropic_efficiency",
                            "value": 0.7,
                            "units": "fraction",
                            "source_tag": "owner-ratify-placeholder:<pump>",
                            "ticket": "COST-PARAM-PUMP",
                            "status": "owner-ratify-placeholder",
                            "ratification_note": "owner must ratify",
                        }],
                        "rows": [{"flow_kg_h": 1.5}],
                    },
                    "warnings": ["allocation <warning>"],
                    "owner_ratify_placeholders": [{
                        "name": "thermal_usd_per_flux_h",
                        "value": 1.0,
                        "units": "USD/(K*h)",
                        "source_tag": "owner-ratify-placeholder:<legacy>",
                        "ticket": "COST-PARAM-THERMAL",
                        "status": "owner-ratify-placeholder",
                    }],
                    "owner_ratify_placeholder_count": 1,
                }
            },
        },
    }


def test_present_rollup_renders_emitted_depth_and_escapes() -> None:
    rendered = _render_panel(_full_artifact())
    html = rendered["html"]

    assert rendered["id"] == "sec-p8-cost-rollup"
    assert "52 USD" in html
    assert "owner &lt;source&gt;" in html
    assert "policy &lt;unsafe&gt;" in html
    assert "Cleaned melt · SiO₂" in html
    assert "Metal pool (bottom) · Fe" in html
    assert "50631 USD" in html or "50,630 USD" in html
    assert "resolved" in html
    assert "allocation &lt;warning&gt;" in html
    assert "owner-ratify-placeholder:&lt;pump&gt;" in html
    assert "<warning>" not in html


def test_absent_rollup_stays_pending() -> None:
    html = _render_panel({"header": {}, "terminal": {}})["html"]

    assert "Pending · terminal.cost_totals" in html
    assert "Pending · header.cost_block" in html
    assert "Pending · terminal.run_metadata.cost_rollup_diagnostic" in html
    assert "0 USD" not in html
    assert "0 kWh" not in html


def test_diagnostic_never_becomes_viewer_price_authority() -> None:
    artifact = _full_artifact()
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    diagnostic["price_basis"] = "owner_ratified_placeholder_free_v2"
    diagnostic["owner_ratify_placeholder_count"] = 0
    diagnostic["owner_ratify_placeholders"] = []

    html = _render_panel(artifact)["html"]

    assert "owner_ratified_placeholder_free_v2" in html
    assert "Diagnostic allocation · not viewer price authority" in html
    assert "This does not make diagnostic projections viewer price authority." in html
    assert "ratified viewer price authority" not in html


def test_partial_totals_and_sparse_maps_do_not_derive() -> None:
    artifact = _full_artifact()
    totals = artifact["terminal"]["cost_totals"]
    totals.pop("total_cost_usd")
    totals["electrical_cost_usd"] = 20.0
    totals["solar_heat_cost_usd"] = 3.0
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    diagnostic["product_costs"] = {}
    diagnostic["active_inventory_costs"] = {}

    html = _render_panel(artifact, energy={"totalCost": 23.0})["html"]

    assert "pending · terminal.cost_totals.total_cost_usd not emitted" in html
    assert "23 USD" not in html
    assert "product_costs has no emitted rows; sparse allocation is not displayed as zero" in html
    assert "active_inventory_costs has no emitted rows; sparse allocation is not displayed as zero" in html


def test_each_partial_canonical_total_stays_pending_without_client_math() -> None:
    cases = [
        ("process_electrical_energy_kWh", "Process electrical energy", "2 kWh"),
        ("process_electrical_cost_usd", "Process electrical cost", "20 USD"),
        ("pumping_electrical_energy_kWh", "Pumping electrical energy", "3 kWh"),
        ("pumping_electrical_cost_usd", "Pumping electrical cost", "30 USD"),
        ("electrical_energy_kWh", "Total electrical energy", "5 kWh"),
        ("electrical_cost_usd", "Total electrical cost", "50 USD"),
        ("evaporation_thermal_energy_kWh", "Evaporation thermal energy", "40 kWh"),
        ("solar_heat_cost_usd", "Solar heat cost", "2 USD"),
        ("total_cost_usd", "Canonical total cost", "52 USD"),
    ]

    for field, label, derived_text in cases:
        artifact = _full_artifact()
        artifact["terminal"]["cost_totals"].pop(field)
        html = _render_panel(artifact, energy={
            "processElectrical": 2.0,
            "pumpingElectrical": 3.0,
            "electrical": 5.0,
            "thermal": 40.0,
        })["html"]
        headline = html.split('<div class="sec-p8-authority">', 1)[0]
        metric = headline.split(f'<div class="k">{label}</div><div class="v">', 1)[1].split("</div></div>", 1)[0]

        assert f"pending · terminal.cost_totals.{field} not emitted" in metric
        assert derived_text not in metric


def test_partial_nested_diagnostics_stay_pending_without_reconstruction() -> None:
    artifact = _full_artifact()
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    diagnostic["owner_ratify_placeholders"] = [
        {"name": "electrical_usd_per_kWh", "value": 10.0},
        {"name": "thermal_usd_per_flux_h", "value": 1.0},
        {"name": "furnace_usd_per_h", "value": 10.0},
        {"name": "launch_usd_per_kg", "value": 10000.0},
        {"name": "reagent_usd_per_kg", "value": 100.0},
    ]
    diagnostic.pop("owner_ratify_placeholder_count")
    diagnostic["product_costs"]["process.cleaned_melt:SiO2"].pop("owner_ratify_money_projection")
    diagnostic["run_input_cost"].pop("owner_ratify_money_projection")
    diagnostic["auxiliary_electrical_diagnostic"].pop("auxiliary_electrical_kWh")
    diagnostic["pumping_diagnostic"].pop("pumping_electrical_kWh")
    diagnostic["pumping_diagnostic"]["rows"] = [
        {"pumping_electrical_kWh": 31.0},
        {"pumping_electrical_kWh": 57.0},
    ]

    html = _render_panel(artifact)["html"]

    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.owner_ratify_placeholder_count not emitted" in html
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.product_costs.process.cleaned_melt:SiO2.owner_ratify_money_projection not emitted" in html
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.run_input_cost.owner_ratify_money_projection not emitted" in html
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.auxiliary_electrical_diagnostic.auxiliary_electrical_kWh not emitted" in html
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.pumping_diagnostic.pumping_electrical_kWh not emitted" in html
    assert "31 kWh" in html
    assert "57 kWh" in html
    assert "88 kWh" not in html
    assert "3.26 kWh" not in html
    assert "40,540 USD" not in html
    assert "141,800 USD" not in html
    assert "88 USD" not in html


def test_no_rows_pumping_zero_is_preserved_but_excluded_from_canonical_totals() -> None:
    artifact = _full_artifact()
    pumping = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["pumping_diagnostic"]
    pumping["status"] = "no_rows"
    pumping["pumping_electrical_kWh"] = 0.0
    pumping["rows"] = []

    html = _render_panel(artifact)["html"]

    assert "Emitted pumping diagnostic energy" in html
    assert "0 kWh" in html
    assert "excluded by the canonical cost-total emitter because status is no_rows" in html


def test_missing_cost_block_price_does_not_adopt_diagnostic_placeholder() -> None:
    artifact = _full_artifact()
    artifact["header"]["cost_block"].pop("electrical_cost_per_kWh")
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    diagnostic["owner_ratify_placeholders"] = [{
        "name": "electrical_usd_per_kWh",
        "value": 99.0,
        "units": "USD/kWh",
        "status": "owner-ratify-placeholder",
    }]

    html = _render_panel(artifact)["html"]
    headline = html.split('<div class="sec-p8-authority">', 1)[0]

    assert "pending · header.cost_block.electrical_cost_per_kWh not emitted" in headline
    assert "99 USD/kWh" not in headline
