from __future__ import annotations

import copy
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
                            "source_tag": "parameter-source:<pump>",
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

    assert rendered["id"] == "sec-p8-cost-rollup"
    assert "Canonical energy cost total" in totals
    assert "Canonical total cost" not in totals
    assert "Canonical energy-cost scope: electrical + evaporation solar heat." in totals
    assert "52 USD" in totals
    assert "pumping included after resolved status" in totals
    assert "owner &lt;source&gt;" in headline
    assert "policy &lt;unsafe&gt;" in _between(html, '<div class="sec-p8-identity">', "</div><details")
    assert "Cleaned melt · SiO₂" in products
    assert "Metal pool (bottom) · Fe" in inventory
    assert "50631 USD" in products or "50,630 USD" in products
    assert "resolved" in _field_value(pumping, "Emitted pumping status")
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

    assert "pending · terminal.cost_totals.total_cost_usd not emitted" in html
    assert "23 USD" not in html
    assert "product_costs was emitted empty; sparse allocation is not displayed as zero" in html
    assert "active_inventory_costs was emitted empty; sparse allocation is not displayed as zero" in html


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
    diagnostic["pumping_diagnostic"]["rows"] = [
        {"pumping_electrical_kWh": 31.0},
        {"pumping_electrical_kWh": 57.0},
    ]

    html = _render_panel(artifact)["html"]
    identity = _between(html, '<div class="sec-p8-identity">', "</div><details")
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    run_input = _between(html, "<summary>Run input cost</summary>", "<summary>Auxiliary electrical diagnostic</summary>")
    auxiliary = _between(html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")
    pumping = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")

    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.owner_ratify_placeholder_count not emitted" in identity
    assert "5" not in _field_value(identity, "Emitted placeholder count")
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.product_costs.process.cleaned_melt:SiO2.owner_ratify_money_projection not emitted" in products
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.run_input_cost.owner_ratify_money_projection not emitted" in run_input
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.auxiliary_electrical_diagnostic.auxiliary_electrical_kWh not emitted" in auxiliary
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.pumping_diagnostic.pumping_electrical_kWh not emitted" in pumping
    assert "31 kWh" in pumping
    assert "57 kWh" in pumping
    assert "88 kWh" not in pumping
    assert "3.26 kWh" not in auxiliary
    assert "40,540 USD" not in products
    assert "141,800 USD" not in run_input
    assert "88 USD" not in pumping


def test_no_rows_pumping_zero_is_preserved_without_viewer_inclusion_claim() -> None:
    artifact = _full_artifact()
    pumping = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]["pumping_diagnostic"]
    pumping["status"] = "no_rows"
    pumping["pumping_electrical_kWh"] = 0.0
    pumping["rows"] = []

    html = _render_panel(artifact)["html"]
    pumping_region = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")

    assert "no_rows" in _field_value(pumping_region, "Emitted pumping status")
    assert "0 kWh" in _field_value(pumping_region, "Emitted pumping diagnostic energy")
    assert "Canonical-total treatment" not in pumping_region
    assert "excluded by the canonical cost-total emitter" not in pumping_region


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

    auxiliary["components_kWh"] = {}
    empty_html = _render_panel(artifact)["html"]
    empty = _between(empty_html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")

    assert "components_kWh was emitted empty; zero is not inferred" in empty

    auxiliary["components_kWh"] = []
    malformed_html = _render_panel(artifact)["html"]
    malformed = _between(malformed_html, "<summary>Auxiliary electrical diagnostic</summary>", "<summary>Pumping diagnostic</summary>")

    assert "components_kWh was emitted malformed; expected an object" in malformed

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

    html = _render_panel(artifact)["html"]
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


def test_partial_cost_vectors_keep_every_missing_leaf_pending() -> None:
    artifact = _full_artifact()
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    product_entry = diagnostic["product_costs"]["process.cleaned_melt:SiO2"]
    product_entry["accumulated_cost"] = {"electrical_kWh": 5.0}
    diagnostic["active_inventory_costs"]["process.metal_phase_bottom_pool:Fe"] = {"electrical_kWh": 6.0}
    diagnostic["run_input_cost"]["physical_cost"] = {"electrical_kWh": 11.0}

    html = _render_panel(artifact)["html"]
    products = _between(html, "<summary>Product allocations</summary>", "<summary>Active inventory allocations</summary>")
    inventory = _between(html, "<summary>Active inventory allocations</summary>", "<summary>Run input cost</summary>")
    run_input = _between(html, "<summary>Run input cost</summary>", "<summary>Auxiliary electrical diagnostic</summary>")

    product_thermal = _field_value(products, "Thermal exposure proxy")
    inventory_furnace = _field_value(inventory, "Furnace time")
    input_launch = _field_value(run_input, "Launch penalty mass")
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.product_costs.process.cleaned_melt:SiO2.accumulated_cost.thermal_flux_h not emitted" in product_thermal
    assert "0 K·h" not in product_thermal
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.active_inventory_costs.process.metal_phase_bottom_pool:Fe.furnace_h not emitted" in inventory_furnace
    assert "0 h" not in inventory_furnace
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.run_input_cost.physical_cost.launch_penalty_kg not emitted" in input_launch
    assert "0 kg" not in input_launch


def test_resolved_pumping_with_rows_but_no_total_does_not_infer_treatment() -> None:
    artifact = _full_artifact()
    diagnostic = artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    diagnostic["pumping_diagnostic"].pop("pumping_electrical_kWh")
    diagnostic["pumping_diagnostic"]["rows"] = [
        {"pumping_electrical_kWh": 1.0},
        {"pumping_electrical_kWh": 2.0},
    ]
    artifact["terminal"].pop("cost_totals")

    html = _render_panel(artifact)["html"]
    pumping = _between(html, "<summary>Pumping diagnostic</summary>", "<summary>Warnings</summary>")

    assert "resolved" in _field_value(pumping, "Emitted pumping status")
    assert "pending · terminal.run_metadata.cost_rollup_diagnostic.pumping_diagnostic.pumping_electrical_kWh not emitted" in _field_value(pumping, "Emitted pumping diagnostic energy")
    assert "1 kWh" in pumping
    assert "2 kWh" in pumping
    assert "3 kWh" not in pumping
    assert "Canonical-total treatment" not in pumping
    assert "eligible for canonical inclusion" not in pumping
    assert "excluded by the canonical cost-total emitter" not in pumping
