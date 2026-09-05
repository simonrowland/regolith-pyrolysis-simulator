from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "web/report_viewer/panels/p8-cost-rollup.js"
PANEL_CSS = ROOT / "web/report_viewer/panels/p8-cost-rollup.css"
LABELS = ROOT / "web/report_viewer/labels.js"
PUMPING_NOTE = (
    "Pumping is a rough diagnostic, not a validated pump design. Canonical pumping treatment is emitted only by "
    "terminal.cost_totals: pumping_electrical_energy_kWh and pumping_electrical_cost_usd record inclusion; optional "
    "basis_note records exclusion or status. Diagnostic status is not reinterpreted here."
)


def _render_panel(
    artifact: object,
    *,
    energy: dict | None = None,
    sentinel_helpers: bool = False,
) -> dict[str, object]:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const context = { console };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const helperCalls = { esc: [], speciesColor: [] };
if (JSON.parse(process.argv[6])) {
  const sharedEsc = context.ReportLabels.esc;
  context.ReportLabels = {
    ...context.ReportLabels,
    esc: value => { helperCalls.esc.push(String(value)); return sharedEsc(value); },
    speciesColor: species => { helperCalls.speciesColor.push(String(species)); return "rgb(1, 2, 3)"; },
  };
}
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const panel = context.ReportPanels[0];
const html = panel.render(JSON.parse(process.argv[4]), [], [], JSON.parse(process.argv[5]));
process.stdout.write(JSON.stringify({ id: panel.id, html, helperCalls }));
"""
    completed = subprocess.run(
        [
            "node", "-", str(LABELS), str(PANEL), json.dumps(artifact),
            json.dumps(energy or {}), json.dumps(sentinel_helpers),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _between(html: str, start: str, end: str) -> str:
    return html.split(start, 1)[1].split(end, 1)[0]


def _field_value(region: str, label: str) -> str:
    return region.split(f"<span>{label}</span><b>", 1)[1].split("</b></div>", 1)[0]


def _tree_value(region: str, label: str) -> str:
    return region.split(f"<dt>{label}</dt><dd>", 1)[1].split("</dd></div>", 1)[0]


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
                },
            "run_metadata": {
                "cost_rollup_diagnostic": {
                    "schema_version": "cost-ledger-v1",
                    "policy_id": "policy <unsafe>",
                    "price_basis": "legacy_placeholder_awaiting_owner_ratification",
                    "transition_count": 7,
                    "transition_balance_max_abs": 1e-13,
                    "import_context": {
                        "mode": "bootstrap_narrative",
                        "import_flag_enabled": True,
                        "available_supplier_species": ["K", "Na"],
                        "classifier_scope": "reporting_only_not_optimizer_gate",
                        "all_options_visible": True,
                    },
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
                            "source_tag": "parameter-source:<pump>",
                            "ticket": "COST-PARAM-PUMP",
                            "status": "owner-ratify-placeholder",
                            "ratification_note": "owner must ratify",
                        }],
                        # Emitter-shaped row keys from simulator/pumping_cost.py::SubambientPumpCost.to_json
                        "rows": [{
                            "energy_kWh": 1.5,
                            "required_pump_speed_m3_s": 2.0,
                            "line_conductance_m3_s": 3.0,
                            "effective_speed_ceiling_m3_s": 4.0,
                        }],
                    },
                    "warnings": ["allocation <warning>"],
                    "owner_ratify_placeholders": [{
                        "name": "thermal_usd_per_flux_h",
                        "value": 1.0,
                        "units": "USD/(K*h)",
                        "source_tag": "parameter-source:<legacy>",
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
    headline = html.split('<div class="sec-p8-authority">', 1)[0]
    totals = headline.split('<div class="card sec-p8-price-card">', 1)[0]
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    inventory = _between(html, "<summary>Active inventory allocations</summary>", "<summary>Run input cost</summary>")
    placeholders = _between(html, "<summary>Owner-ratification placeholders</summary>", "<summary>Product allocations</summary>")
    pumping = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")

    identity = _between(html, '<div class="sec-p8-identity">', "</div><details")
    auxiliary = _between(html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")
    run_input = _between(html, "<summary>Run input cost</summary>", "<summary>Auxiliary electrical diagnostic</summary>")

    assert rendered["id"] == "sec-p8-cost-rollup"
    assert "Canonical energy cost total" in totals
    assert "Canonical total cost" not in totals
    assert "Canonical energy-cost scope: electrical + evaporation solar heat." in totals
    assert "52 USD" in totals
    assert "pending · terminal.cost_totals.basis_note not emitted" in totals
    assert "Emitted canonical energy-cost totals use these artifact price inputs" in headline
    assert "owner &lt;source&gt;" in headline
    assert "policy &lt;unsafe&gt;" in identity
    residual = _field_value(identity, "Maximum absolute transition residual · native cost-vector component")
    assert "cost-ledger-v1" in _field_value(identity, "Schema version")
    assert "7" in _field_value(identity, "Cost-ledger transitions")
    assert "1.00e-13" in residual
    assert "Cleaned melt · SiO₂" in products
    assert "Metal pool (bottom) · Fe" in inventory
    assert "50631 USD" in products or "50,630 USD" in products
    assert "resolved" in _field_value(pumping, "Emitted pumping status")
    assert "true" in _tree_value(pumping, "Feasible")
    assert "pumping-cost-rollup-v1" in _tree_value(pumping, "Schema version")
    assert "auxiliary-electrical-rollup-v1" in _tree_value(auxiliary, "Schema version")
    assert "Required pump speed" in pumping
    assert "2 m³/s" in pumping
    assert "Line conductance" in pumping
    assert "3 m³/s" in pumping
    assert "Effective speed ceiling" in pumping
    assert "4 m³/s" in pumping
    assert "m3 s" not in pumping  # underscore→space must not mangle m3_s units
    assert "1.5 kWh" in pumping  # per-row energy_kWh, not invented row key
    assert "fraction" in _tree_value(pumping, "Units")
    assert "COST-PARAM-PUMP" in _tree_value(pumping, "Ticket")
    assert "owner must ratify" in _tree_value(pumping, "Ratification note")
    assert "thermal_flux_h = absolute_temperature_K * duration_h" in _field_value(run_input, "Thermal proxy definition")
    assert PUMPING_NOTE in pumping
    # Exact approved pumping note only — no appended inclusion reinterpretation.
    assert pumping.count(PUMPING_NOTE) == 1
    assert "Status resolved means pumping is included" not in pumping
    assert "Canonical-total treatment" not in pumping
    assert "eligible for canonical inclusion" not in pumping
    assert "owner-ratify-placeholder" in _tree_value(placeholders, "Status")
    assert "owner-ratify-placeholder" in _tree_value(pumping, "Status")
    assert "allocation &lt;warning&gt;" in html
    assert "parameter-source:&lt;pump&gt;" in pumping
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
    authority = _between(html, '<div class="sec-p8-authority">', '<details class="sec-p8-details">')
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    run_input = _between(html, "<summary>Run input cost</summary>", "<summary>Auxiliary electrical diagnostic</summary>")

    assert "owner_ratified_placeholder_free_v2" in _field_value(authority, "Emitted diagnostic price basis")
    assert "Diagnostic allocation · not viewer price authority" in authority
    assert "Money projections use the emitted diagnostic price basis shown above" in authority
    assert "emitted legacy-placeholder basis" not in authority
    assert "Diagnostic money projection · not viewer price authority" in products
    assert "Diagnostic money projection · not viewer price authority" in run_input
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
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    inventory = _between(html, "<summary>Active inventory allocations</summary>", "<summary>Run input cost</summary>")

    price_caption = html.split('<div class="card sec-p8-price-card">', 1)[1].split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
    assert "pending · terminal.cost_totals.total_cost_usd not emitted" in html
    assert "23 USD" not in html
    # Incomplete core (missing total_cost_usd) is not a typed canonical result → no binding claim.
    assert "Emitted canonical energy-cost totals use these artifact price inputs" not in price_caption
    assert "no complete canonical energy-cost field set is emitted" in price_caption
    assert "product_costs was emitted empty; sparse allocation is not displayed as zero" in html
    assert "active_inventory_costs was emitted empty; sparse allocation is not displayed as zero" in html
    assert ">0 USD<" not in products
    assert ">0 USD<" not in inventory


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
        ("total_cost_usd", "Canonical energy cost total", "52 USD"),
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

    malformed_artifact = _full_artifact()
    malformed_artifact["terminal"]["cost_totals"]["total_cost_usd"] = None
    malformed_headline = _render_panel(malformed_artifact)["html"].split('<div class="sec-p8-authority">', 1)[0]
    malformed_metric = malformed_headline.split('<div class="k">Canonical energy cost total</div><div class="v">', 1)[1].split("</div></div>", 1)[0]
    assert "malformed · terminal.cost_totals.total_cost_usd expected a finite number" in malformed_metric
    assert "not emitted" not in malformed_metric

    zero_artifact = _full_artifact()
    zero_artifact["terminal"]["cost_totals"]["total_cost_usd"] = 0.0
    zero_headline = _render_panel(zero_artifact)["html"].split('<div class="sec-p8-authority">', 1)[0]
    zero_metric = zero_headline.split('<div class="k">Canonical energy cost total</div><div class="v">', 1)[1].split("</div></div>", 1)[0]
    assert "0 USD" in zero_metric
    assert "pending" not in zero_metric


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
    # Emitter-shaped row energy key (simulator/pumping_cost.py) — not invented pumping_electrical_kWh.
    diagnostic["pumping_diagnostic"]["rows"] = [
        {"energy_kWh": 31.0},
        {"energy_kWh": 57.0},
    ]

    html = _render_panel(artifact)["html"]
    identity = _between(html, '<div class="sec-p8-identity">', "</div><details")
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    run_input = _between(html, "<summary>Run input cost</summary>", "<summary>Auxiliary electrical diagnostic</summary>")
    auxiliary = _between(html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")
    pumping = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")
    product_money = _field_value(products, "Diagnostic money projection · not viewer price authority")
    input_money = _field_value(run_input, "Diagnostic money projection · not viewer price authority")
    top_energy = _field_value(pumping, "Emitted pumping diagnostic energy")

    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.owner_ratify_placeholder_count not emitted" in identity
    assert "5" not in _field_value(identity, "Emitted placeholder count")
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.product_costs.process.cleaned_melt:SiO2.owner_ratify_money_projection not emitted" in product_money
    assert "USD" not in product_money
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.run_input_cost.owner_ratify_money_projection not emitted" in input_money
    assert "USD" not in input_money
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.auxiliary_electrical_diagnostic.auxiliary_electrical_kWh not emitted" in auxiliary
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.pumping_diagnostic.pumping_electrical_kWh not emitted" in top_energy
    assert "88 kWh" not in top_energy
    assert "31 kWh" in pumping
    assert "57 kWh" in pumping
    assert "88 kWh" not in pumping
    assert "3.26 kWh" not in auxiliary
    assert "40,542 USD" not in products
    assert "141,752 USD" not in run_input
    assert "880 USD" not in pumping


def test_no_rows_pumping_zero_is_preserved_without_viewer_inclusion_claim() -> None:
    artifact = _full_artifact()
    pumping = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["pumping_diagnostic"]
    pumping["status"] = "no_rows"
    pumping["pumping_electrical_kWh"] = 0.0
    pumping["rows"] = []
    totals = artifact["terminal"]["cost_totals"]
    totals.pop("pumping_electrical_energy_kWh")
    totals.pop("pumping_electrical_cost_usd")
    totals["electrical_energy_kWh"] = 2.0
    totals["electrical_cost_usd"] = 20.0
    totals["total_cost_usd"] = 22.0
    totals["basis_note"] = "pumping electrical energy excluded; diagnostic status=no_rows"

    html = _render_panel(artifact)["html"]
    pumping_region = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")
    totals_region = html.split('<div class="card sec-p8-price-card">', 1)[0]

    assert "no_rows" in _field_value(pumping_region, "Emitted pumping status")
    assert "0 kWh" in _field_value(pumping_region, "Emitted pumping diagnostic energy")
    assert PUMPING_NOTE in pumping_region
    assert "pumping electrical energy excluded; diagnostic status=no_rows" in totals_region
    assert "pending · terminal.cost_totals.pumping_electrical_energy_kWh not emitted" in totals_region
    assert "pending · terminal.cost_totals.pumping_electrical_cost_usd not emitted" in totals_region


def test_unavailable_pumping_energy_is_shown_with_reason_not_zero() -> None:
    artifact = _full_artifact()
    pumping = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["pumping_diagnostic"]
    pumping["status"] = "refused"
    pumping["reason"] = "missing-o2-vented-flow"
    pumping["pumping_electrical_kWh"] = {
        "status": "unavailable",
        "reason": "missing-o2-vented-flow",
        "value": None,
        "units": "kWh",
    }
    pumping["rows"] = []
    product = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["product_costs"][
        "process.cleaned_melt:SiO2"
    ]
    product["owner_ratify_money_projection"] = {
        "status": "unavailable",
        "reason": "missing-o2-vented-flow",
        "value": None,
        "units": "USD",
    }
    totals = artifact["terminal"]["cost_totals"]
    totals["pumping_electrical_energy_kWh"] = {
        "status": "unavailable",
        "reason": "missing-o2-vented-flow",
        "value": None,
        "units": "kWh",
    }
    totals["pumping_electrical_cost_usd"] = {
        "status": "unavailable",
        "reason": "missing-o2-vented-flow",
        "value": None,
        "units": "USD",
    }
    totals["electrical_energy_kWh"] = {
        "status": "unavailable",
        "reason": "missing-o2-vented-flow",
        "value": None,
        "units": "kWh",
    }
    totals["total_cost_usd"] = {
        "status": "unavailable",
        "reason": "missing-o2-vented-flow",
        "value": None,
        "units": "USD",
    }

    html = _render_panel(artifact)["html"]
    pumping_region = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    totals_region = html.split('<div class="card sec-p8-price-card">', 1)[0]

    assert "unavailable · missing-o2-vented-flow" in _field_value(
        pumping_region, "Emitted pumping diagnostic energy"
    )
    assert "0 kWh" not in _field_value(pumping_region, "Emitted pumping diagnostic energy")
    assert "unavailable · missing-o2-vented-flow" in products
    assert "unavailable · missing-o2-vented-flow" in totals_region


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
    price_card = headline.split('<div class="card sec-p8-price-card">', 1)[1]

    assert "pending · header.cost_block.electrical_cost_per_kWh not emitted" in headline
    assert "99 USD/kWh" not in headline
    assert "Canonical-total price provenance cannot be validated from this artifact" in price_card
    assert "Canonical energy-cost totals use these artifact price inputs" not in price_card


def test_price_basis_absent_empty_and_malformed_states_do_not_claim_legacy() -> None:
    artifact = _full_artifact()
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    diagnostic.pop("price_basis")
    absent_html = _render_panel(artifact)["html"]
    absent = _between(absent_html, '<div class="sec-p8-authority">', '<details class="sec-p8-details">')

    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.price_basis not emitted" in _field_value(absent, "Emitted diagnostic price basis")
    assert "Diagnostic price basis is not emitted" in absent
    assert "emitted legacy-placeholder basis" not in absent

    diagnostic["price_basis"] = ""
    empty_html = _render_panel(artifact)["html"]
    empty = _between(empty_html, '<div class="sec-p8-authority">', '<details class="sec-p8-details">')

    assert "empty · terminal.run_metadata.cost_rollup_diagnostic.price_basis emitted an empty string" in _field_value(empty, "Emitted diagnostic price basis")
    assert "Diagnostic price basis was emitted empty" in empty
    assert "emitted legacy-placeholder basis" not in empty

    diagnostic["price_basis"] = {"unexpected": "object"}
    malformed_html = _render_panel(artifact)["html"]
    malformed = _between(malformed_html, '<div class="sec-p8-authority">', '<details class="sec-p8-details">')

    assert "malformed · terminal.run_metadata.cost_rollup_diagnostic.price_basis expected a scalar" in _field_value(malformed, "Emitted diagnostic price basis")
    assert "Diagnostic price basis was emitted malformed" in malformed
    assert "emitted legacy-placeholder basis" not in malformed


def test_auxiliary_total_and_components_have_independent_partial_and_four_states() -> None:
    artifact = _full_artifact()
    auxiliary = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["auxiliary_electrical_diagnostic"]
    auxiliary["auxiliary_electrical_kWh"] = 8.0
    auxiliary.pop("components_kWh")
    absent_html = _render_panel(artifact)["html"]
    absent = _between(absent_html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")

    assert "8 kWh" in _field_value(absent, "Emitted auxiliary electrical energy")
    assert "Pending · terminal.run_metadata.cost_rollup_diagnostic.auxiliary_electrical_diagnostic.components_kWh" in absent
    assert ">0 kWh<" not in absent

    auxiliary["components_kWh"] = {}
    empty_html = _render_panel(artifact)["html"]
    empty = _between(empty_html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")

    assert "components_kWh was emitted empty; zero is not inferred" in empty
    assert ">0 kWh<" not in empty

    auxiliary["components_kWh"] = []
    malformed_html = _render_panel(artifact)["html"]
    malformed = _between(malformed_html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")

    assert "components_kWh was emitted malformed; expected an object" in malformed
    assert ">0 kWh<" not in malformed

    auxiliary["components_kWh"] = {"condenser": 0.0}
    zero_html = _render_panel(artifact)["html"]
    zero = _between(zero_html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")

    assert "0 kWh" in _tree_value(zero, "Condenser")

    auxiliary["components_kWh"] = {"condenser": 0.25, "pumping": 3.0, "turbine": 0.01}
    auxiliary.pop("auxiliary_electrical_kWh")
    inverse_html = _render_panel(artifact)["html"]
    inverse = _between(inverse_html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")

    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.auxiliary_electrical_diagnostic.auxiliary_electrical_kWh not emitted" in _field_value(inverse, "Emitted auxiliary electrical energy")
    assert "0.25 kWh" in inverse
    assert "3 kWh" in inverse
    assert "0.01 kWh" in inverse
    assert "3.26 kWh" not in inverse


def test_hostile_map_keys_escape_once_and_two_same_map_bins_survive() -> None:
    artifact = _full_artifact()
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    product = next(iter(diagnostic["product_costs"].values()))
    hostile_account = "process.<img src=x onerror=alert(1)>&account:SiO2"
    hostile_species = "oxygen_mre_anode_stored:O2<script>alert(2)</script>"
    diagnostic["product_costs"] = {
        hostile_account: copy.deepcopy(product),
        hostile_species: copy.deepcopy(product),
    }

    rendered = _render_panel(artifact, sentinel_helpers=True)
    html = rendered["html"]
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    summaries = [part.split("</summary>", 1)[0] for part in products.split("<summary>")[1:]]
    account_summary, species_summary = summaries

    assert 'title="process.&lt;img src=x onerror=alert(1)&gt;&amp;account:SiO2"' in account_summary
    assert "&lt;Img Src=X Onerror=Alert(1)&gt;&amp;Account · SiO₂" in account_summary
    assert "<Img" not in account_summary
    assert "&amp;lt;Img" not in account_summary
    assert 'title="oxygen_mre_anode_stored:O2&lt;script&gt;alert(2)&lt;/script&gt;"' in species_summary
    assert "Oxygen Mre Anode Stored · O2&lt;script&gt;alert(2)&lt;/script&gt;" in species_summary
    assert "<script>" not in species_summary
    assert "&amp;lt;script" not in species_summary
    assert hostile_account in rendered["helperCalls"]["esc"]
    # Visible account labels must also pass through shared esc (not a local identity).
    assert any("Img Src=X Onerror=Alert(1)" in call or "img src=x" in call
               for call in rendered["helperCalls"]["esc"])
    assert "SiO2" in rendered["helperCalls"]["speciesColor"]
    assert "O2<script>alert(2)</script>" in rendered["helperCalls"]["speciesColor"]
    # Sentinel colour return must appear in rendered marker style (not discarded).
    assert 'style="background:rgb(1, 2, 3)"' in products


def test_partial_cost_vectors_keep_every_missing_leaf_pending() -> None:
    fields = [
        ("electrical_kWh", "Electrical energy", "kWh"),
        ("thermal_flux_h", "Thermal exposure proxy", "K·h"),
        ("furnace_h", "Furnace time", "h"),
        ("launch_penalty_kg", "Launch penalty mass", "kg"),
        ("external_reagent_kg", "External reagent mass", "kg"),
    ]
    vectors = [
        (
            "product_costs.process.cleaned_melt:SiO2.accumulated_cost",
            lambda diagnostic: diagnostic["product_costs"]["process.cleaned_melt:SiO2"]["accumulated_cost"],
            ("<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>"),
        ),
        (
            "active_inventory_costs.process.metal_phase_bottom_pool:Fe",
            lambda diagnostic: diagnostic["active_inventory_costs"]["process.metal_phase_bottom_pool:Fe"],
            ("<summary>Active inventory allocations</summary>", "<summary>Run input cost</summary>"),
        ),
        (
            "run_input_cost.physical_cost",
            lambda diagnostic: diagnostic["run_input_cost"]["physical_cost"],
            ("<summary>Run input cost</summary>", "<summary>Auxiliary electrical diagnostic</summary>"),
        ),
    ]

    for vector_path, vector_getter, boundaries in vectors:
        for field, label, unit in fields:
            artifact = _full_artifact()
            diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
            vector_getter(diagnostic).pop(field)
            region = _between(_render_panel(artifact)["html"], *boundaries)
            value = _field_value(region, label)

            assert f"pending · terminal.run_metadata.cost_rollup_diagnostic.{vector_path}.{field} not emitted" in value
            assert f"0 {unit}" not in value


def test_resolved_pumping_with_rows_but_no_total_does_not_infer_treatment() -> None:
    artifact = _full_artifact()
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    diagnostic["pumping_diagnostic"].pop("pumping_electrical_kWh")
    # Real emitter row key is energy_kWh (SubambientPumpCost.to_json), not pumping_electrical_kWh.
    diagnostic["pumping_diagnostic"]["rows"] = [
        {"energy_kWh": 1.0},
        {"energy_kWh": 2.0},
    ]
    artifact["terminal"].pop("cost_totals")

    html = _render_panel(artifact)["html"]
    pumping = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")
    totals = html.split('<div class="card sec-p8-price-card">', 1)[0]
    top_energy = _field_value(pumping, "Emitted pumping diagnostic energy")

    assert "resolved" in _field_value(pumping, "Emitted pumping status")
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.pumping_diagnostic.pumping_electrical_kWh not emitted" in top_energy
    assert "3 kWh" not in top_energy
    assert "1 kWh" in pumping
    assert "2 kWh" in pumping
    assert "3 kWh" not in pumping
    assert PUMPING_NOTE in pumping
    assert "Pending · terminal.cost_totals" in totals
    assert "30 USD" not in totals


def test_missing_cost_basis_note_is_not_reconstructed_from_pumping_inputs() -> None:
    artifact = _full_artifact()

    html = _render_panel(artifact)["html"]
    totals = html.split('<div class="card sec-p8-price-card">', 1)[0]
    pumping = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")

    assert "pending · terminal.cost_totals.basis_note not emitted" in totals
    assert "pumping included after resolved status" not in totals
    assert PUMPING_NOTE in pumping


def test_price_caption_requires_emitted_totals_and_complete_provenance() -> None:
    artifact = _full_artifact()
    html = _render_panel(artifact)["html"]
    price_card = html.split('<div class="card sec-p8-price-card">', 1)[1].split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
    assert "Emitted canonical energy-cost totals use these artifact price inputs" in price_card

    artifact["terminal"].pop("cost_totals")
    no_totals_html = _render_panel(artifact)["html"]
    no_totals_card = no_totals_html.split('<div class="card sec-p8-price-card">', 1)[1]
    no_totals_caption = no_totals_card.split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
    assert "canonical energy-cost totals are not emitted in this artifact" in no_totals_caption
    assert "Emitted canonical energy-cost totals use these artifact price inputs" not in no_totals_caption

    totals_states = [
        ({}, "canonical energy-cost totals were emitted empty"),
        (None, "canonical energy-cost totals were emitted null"),
        ([], "canonical energy-cost totals were emitted malformed"),
    ]
    for totals_value, expected_caption in totals_states:
        partial = _full_artifact()
        partial["terminal"]["cost_totals"] = totals_value
        partial_html = _render_panel(partial)["html"]
        partial_caption = partial_html.split('<div class="card sec-p8-price-card">', 1)[1].split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
        assert expected_caption in partial_caption
        assert "Emitted canonical energy-cost totals use these artifact price inputs" not in partial_caption

    # Non-empty partial / foreign-key / null-only bags must not claim price binding (F1 / codex P1).
    for partial_totals in (
        {"unexpected": True},
        {"basis_note": "only-note"},
        {"total_cost_usd": None},
        {"basis_note": "not canonical", "foreign": 1},
    ):
        bag = _full_artifact()
        bag["terminal"]["cost_totals"] = partial_totals
        bag_html = _render_panel(bag)["html"]
        bag_caption = bag_html.split('<div class="card sec-p8-price-card">', 1)[1].split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
        totals_region = bag_html.split('<div class="card sec-p8-price-card">', 1)[0]
        assert "no complete canonical energy-cost field set is emitted" in bag_caption or \
            "no price-to-total binding is inferred" in bag_caption
        assert "Emitted canonical energy-cost totals use these artifact price inputs" not in bag_caption
        assert "Canonical energy-cost scope: electrical + evaporation solar heat." not in totals_region
        assert "no canonical energy-cost fields are emitted" in totals_region or \
            "pending · terminal.cost_totals.total_cost_usd not emitted" in totals_region

    # Incomplete prices must nest totals state — not claim totals remain emitted when absent (F2).
    incomplete_absent = _full_artifact()
    incomplete_absent["header"]["cost_block"].pop("solar_heat_cost_per_kWh")
    incomplete_absent["terminal"].pop("cost_totals")
    incomplete_absent_html = _render_panel(incomplete_absent)["html"]
    incomplete_absent_caption = incomplete_absent_html.split('<div class="card sec-p8-price-card">', 1)[1].split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
    assert "Canonical-total price provenance cannot be validated from this artifact" in incomplete_absent_caption
    assert "canonical energy-cost totals are not emitted in this artifact" in incomplete_absent_caption
    assert "canonical totals remain artifact-emitted values" not in incomplete_absent_caption

    incomplete_partial = _full_artifact()
    incomplete_partial["header"]["cost_block"].pop("solar_heat_cost_per_kWh")
    incomplete_partial["terminal"]["cost_totals"] = {"basis_note": "only"}
    incomplete_partial_html = _render_panel(incomplete_partial)["html"]
    incomplete_partial_caption = incomplete_partial_html.split('<div class="card sec-p8-price-card">', 1)[1].split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
    assert "no complete canonical energy-cost field set is emitted" in incomplete_partial_caption
    assert "canonical totals remain artifact-emitted values" not in incomplete_partial_caption

    for provenance in (None, "", []):
        incomplete = _full_artifact()
        if provenance is None:
            incomplete["header"]["cost_block"].pop("provenance")
        else:
            incomplete["header"]["cost_block"]["provenance"] = provenance
        incomplete_html = _render_panel(incomplete)["html"]
        incomplete_caption = incomplete_html.split('<div class="card sec-p8-price-card">', 1)[1].split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
        assert "Canonical-total price provenance cannot be validated from this artifact" in incomplete_caption
        assert "canonical totals remain artifact-emitted values" in incomplete_caption  # full totals still present
        assert "Emitted canonical energy-cost totals use these artifact price inputs" not in incomplete_caption

    zero_prices = _full_artifact()
    zero_prices["header"]["cost_block"]["electrical_cost_per_kWh"] = 0.0
    zero_prices["header"]["cost_block"]["solar_heat_cost_per_kWh"] = 0.0
    zero_html = _render_panel(zero_prices)["html"]
    zero_card = zero_html.split('<div class="card sec-p8-price-card">', 1)[1]
    zero_caption = zero_card.split('<p class="sec-p8-caption">', 1)[1].split("</p>", 1)[0]
    assert "0 USD/kWh" in _field_value(zero_card, "Electrical price")
    assert "0 USD/kWh" in _field_value(zero_card, "Solar heat price")
    assert "Emitted canonical energy-cost totals use these artifact price inputs" in zero_caption

    # Provenance null is empty-null, not pending-absent.
    null_prov = _full_artifact()
    null_prov["header"]["cost_block"]["provenance"] = None
    null_prov_html = _render_panel(null_prov)["html"]
    null_prov_card = null_prov_html.split('<div class="card sec-p8-price-card">', 1)[1]
    assert "empty · header.cost_block.provenance emitted null" in _field_value(null_prov_card, "Provenance")
    assert "Canonical-total price provenance cannot be validated from this artifact" in null_prov_card


def test_import_context_preserves_authority_values_and_four_states() -> None:
    artifact = _full_artifact()
    html = _render_panel(artifact)["html"]
    context = _between(html, "<summary>Import classification context</summary>", "<summary>Owner-ratification placeholders</summary>")
    assert "bootstrap_narrative" in _tree_value(context, "Mode")
    assert _tree_value(context, "Import flag enabled") == "true"
    assert "K" in _tree_value(context, "Available supplier species")
    assert "Na" in _tree_value(context, "Available supplier species")
    assert "reporting_only_not_optimizer_gate" in _tree_value(context, "Classifier scope")
    assert _tree_value(context, "All options visible") == "true"

    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    diagnostic.pop("import_context")
    absent_html = _render_panel(artifact)["html"]
    absent = _between(absent_html, "<summary>Import classification context</summary>", "<summary>Owner-ratification placeholders</summary>")
    assert "import_context is not emitted; import-classification authority is unavailable" in absent
    assert "reporting_only_not_optimizer_gate" not in absent

    diagnostic["import_context"] = {}
    empty_html = _render_panel(artifact)["html"]
    empty = _between(empty_html, "<summary>Import classification context</summary>", "<summary>Owner-ratification placeholders</summary>")
    assert "import_context was emitted empty; import-classification authority is not inferred" in empty

    diagnostic["import_context"] = []
    malformed_html = _render_panel(artifact)["html"]
    malformed = _between(malformed_html, "<summary>Import classification context</summary>", "<summary>Owner-ratification placeholders</summary>")
    assert "import_context was emitted malformed; expected an object" in malformed

    diagnostic["import_context"] = {
        "mode": "bootstrap_narrative",
        "import_flag_enabled": False,
        "available_supplier_species": [],
        "classifier_scope": "reporting_only_not_optimizer_gate",
        "all_options_visible": False,
    }
    false_html = _render_panel(artifact)["html"]
    false_context = _between(false_html, "<summary>Import classification context</summary>", "<summary>Owner-ratification placeholders</summary>")
    assert _tree_value(false_context, "Import flag enabled") == "false"
    assert _tree_value(false_context, "All options visible") == "false"
    assert "Emitted empty list" in _tree_value(false_context, "Available supplier species")


def test_emitted_mass_units_remain_kg() -> None:
    html = _render_panel(_full_artifact())["html"]
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    inventory = _between(html, "<summary>Active inventory allocations</summary>", "<summary>Run input cost</summary>")
    run_input = _between(html, "<summary>Run input cost</summary>", "<summary>Auxiliary electrical diagnostic</summary>")

    assert "4 kg" in _field_value(products, "Product quantity")
    assert "4 kg" in _field_value(products, "Launch penalty mass")
    assert "5 kg" in _field_value(products, "External reagent mass")
    assert "9 kg" in _field_value(inventory, "Launch penalty mass")
    assert "10 kg" in _field_value(inventory, "External reagent mass")
    assert "14 kg" in _field_value(run_input, "Launch penalty mass")
    assert "15 kg" in _field_value(run_input, "External reagent mass")
    assert "mol" not in products + inventory + run_input


def test_css_selectors_are_scoped_to_panel_root() -> None:
    html = _render_panel({})["html"]
    assert '<section class="sec-p8-cost-rollup" id="sec-p8-cost-rollup">' in html
    css = PANEL_CSS.read_text(encoding="utf-8")
    selectors = [
        selector.strip()
        for selector_group in re.findall(r"([^{}]+)\{", css)
        if not selector_group.strip().startswith("@")
        for selector in selector_group.split(",")
    ]
    assert selectors
    assert all(selector.startswith(".sec-p8-cost-rollup") for selector in selectors)


def test_malformed_roots_render_pending_without_throwing() -> None:
    for artifact in (None, 0, "malformed", [], True, {"terminal": {}}):
        rendered = _render_panel(artifact)
        html = rendered["html"]
        assert rendered["id"] == "sec-p8-cost-rollup"
        assert '<section class="sec-p8-cost-rollup" id="sec-p8-cost-rollup">' in html
        assert "Pending · terminal.cost_totals" in html
        assert "Pending · header.cost_block" in html
        assert "Pending · terminal.run_metadata.cost_rollup_diagnostic" in html


def test_same_species_oxygen_dual_bins_remain_distinct() -> None:
    """AGENTS.md: oxygen_mre_anode_stored vs oxygen_melt_offgas_stored stay distinct.

    Mutation this catches: collapse map entries by species (last wins) before render.
    """
    artifact = _full_artifact()
    product = next(iter(artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["product_costs"].values()))
    mre = copy.deepcopy(product)
    offgas = copy.deepcopy(product)
    mre["quantity_kg"] = 1.0
    offgas["quantity_kg"] = 2.0
    artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["product_costs"] = {
        "terminal.oxygen_mre_anode_stored:O2": mre,
        "terminal.oxygen_melt_offgas_stored:O2": offgas,
    }

    html = _render_panel(artifact)["html"]
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    summaries = [part.split("</summary>", 1)[0] for part in products.split("<summary>")[1:]]

    assert len(summaries) == 2
    assert any("O₂ stored · MRE anode" in s and "O₂" in s for s in summaries)
    assert any("O₂ stored · melt offgas" in s for s in summaries)
    # Distinct quantities survive; collapse-by-species would keep only one entry.
    assert products.count("1 kg") >= 1
    assert products.count("2 kg") >= 1
    assert 'title="terminal.oxygen_mre_anode_stored:O2"' in products
    assert 'title="terminal.oxygen_melt_offgas_stored:O2"' in products


def test_pumping_volumetric_fields_use_cubic_metres_per_second() -> None:
    """Emitter pumping rows use *_m3_s volumetric speed/conductance (pumping_cost.py).

    Mutation this catches: readableKey underscore→space yields 'm3 s' instead of m³/s.
    """
    artifact = _full_artifact()
    artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["pumping_diagnostic"]["rows"] = [{
        "required_pump_speed_m3_s": 2.0,
        "line_conductance_m3_s": 3.0,
        "effective_speed_ceiling_m3_s": 4.0,
    }]
    pumping = _between(
        _render_panel(artifact)["html"],
        "<summary>Pumping diagnostic</summary>",
        "<summary>Warnings</summary>",
    )
    assert "Required pump speed" in pumping
    assert "2 m³/s" in _tree_value(pumping, "Required pump speed")
    assert "3 m³/s" in _tree_value(pumping, "Line conductance")
    assert "4 m³/s" in _tree_value(pumping, "Effective speed ceiling")
    assert "m3 s" not in pumping
    assert "m³ s" not in pumping


def test_thermal_proxy_absent_is_not_hardcoded_from_formula_knowledge() -> None:
    """Partial path: thermal_proxy ingredients known elsewhere, leaf absent → pending.

    Mutation this catches: hardcode thermal_proxy formula when key absent.
    """
    artifact = _full_artifact()
    run_input = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["run_input_cost"]
    run_input.pop("thermal_proxy")
    region = _between(
        _render_panel(artifact)["html"],
        "<summary>Run input cost</summary>",
        "<summary>Auxiliary electrical diagnostic</summary>",
    )
    value = _field_value(region, "Thermal proxy definition")
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.run_input_cost.thermal_proxy not emitted" in value
    assert "absolute_temperature_K" not in value
    assert "thermal_flux_h =" not in value


def test_coverage_fields_render_emitted_authority_values() -> None:
    """Mandatory diagnostic coverage must surface emitted values, not just exist in source.

    Mutations this catches (codex P2): drop transition_count / residual rows; exclude
    schema_version or feasible from rest filters.
    """
    html = _render_panel(_full_artifact())["html"]
    identity = _between(html, '<div class="sec-p8-identity">', "</div><details")
    auxiliary = _between(html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")
    pumping = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")

    assert "cost-ledger-v1" in _field_value(identity, "Schema version")
    assert "7" in _field_value(identity, "Cost-ledger transitions")
    assert "1.00e-13" in _field_value(identity, "Maximum absolute transition residual · native cost-vector component")
    assert "auxiliary-electrical-rollup-v1" in _tree_value(auxiliary, "Schema version")
    assert "pumping-cost-rollup-v1" in _tree_value(pumping, "Schema version")
    assert "true" in _tree_value(pumping, "Feasible")
