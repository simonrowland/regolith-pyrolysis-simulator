from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

from simulator.three_product_report import classify_products


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "web" / "report_viewer"


def _run_panel(artifact: dict, index: int | None = None) -> dict:
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const stateNode = { innerHTML: "" };
const statusNode = { textContent: "" };
const context = {
  document: {
    getElementById(id) {
      if (id === "p15-equipment-state") return stateNode;
      if (id === "p15-equipment-status") return statusNode;
      return null;
    }
  },
  console
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
const artifact = JSON.parse(process.argv[4]);
const panel = context.ReportPanels.find((entry) => entry.id === "sec-p15-equipment-diagram");
const html = panel.render(artifact, [], [], {});
if (process.argv[5] !== "none") panel.onTimestep(artifact, Number(process.argv[5]));
process.stdout.write(JSON.stringify({
  id: panel.id,
  hasOnTimestep: typeof panel.onTimestep === "function",
  html,
  stateHtml: stateNode.innerHTML,
  statusText: statusNode.textContent
}));
"""
    completed = subprocess.run(
        [
            "node",
            "-",
            str(VIEWER / "labels.js"),
            str(VIEWER / "panels" / "p15-equipment-diagram.js"),
            json.dumps(artifact),
            "none" if index is None else str(index),
        ],
        input=harness,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _stage_card(html: str, key: str) -> str:
    match = re.search(
        rf'<article class="[^"]*" data-stage="{re.escape(key)}".*?</article>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _account_segment(html: str, account: str) -> str:
    match = re.search(
        rf'<div class="sec-p15-cryo-segment[^"]*" data-account-slot="{re.escape(account)}".*?</div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _pipe_segment(html: str, segment: str) -> str:
    match = re.search(
        rf'<div class="sec-p15-pipe" data-segment="{re.escape(segment)}".*?</div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _tap_card(html: str, pool: str) -> str:
    match = re.search(
        rf'<div class="sec-p15-tap" data-pool="{re.escape(pool)}">.*?</(?:ul|div)></div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _readout(html: str, key: str) -> str:
    match = re.search(
        rf'<div class="sec-p15-readout" data-readout="{re.escape(key)}">.*?</small></div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _selected_timestep(html: str) -> str:
    match = re.search(
        r'<div class="sec-p15-now">.*?</small></div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _source_o2(html: str) -> str:
    match = re.search(
        r'<div class="sec-p15-source-o2(?: [^"]*)?">.*?</div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _gas_dome(html: str) -> str:
    match = re.search(
        r'<div class="sec-p15-dome">.*?</div></div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _vapor(html: str) -> str:
    match = re.search(
        r'<div class="sec-p15-vapor[^"]*"[^>]*>.*?</div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _authority(html: str) -> str:
    match = re.search(
        r'<div class="sec-p15-authority">.*?</div></div>',
        html,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(0)


def _stage_purity() -> dict:
    return {
        "stage_0": {
            "stage_number": 0,
            "label": "Hot Duct",
            "designated_species_kg": {},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": 0.0,
            "impurity_kg": 0.0,
            "total_kg": 0.0,
            "purity_fraction": None,
            "verdict": "INDETERMINATE",
            "reason": "no_captured_mass",
            "warning": "",
        },
        "stage_1_fe_condenser": {
            "stage_number": 1,
            "label": "Fe Condenser",
            "designated_species_kg": {"Fe": 0.000005},
            "coproduct_species_kg": {},
            "impurity_species_kg": {"Si": 0.000002},
            "designated_kg": 0.000005,
            "impurity_kg": 0.000002,
            "total_kg": 0.000007,
            "purity_fraction": 0.7142857,
            "verdict": "CONTAMINATED",
            "warning": "non-designated condensate present",
        }
    }


def _flagged_silica_product_classification() -> dict:
    authority = {
        "species_id": "SiO",
        "pressure": {"kind": "value", "pa": 1.0},
        "flux": {"kind": "eligible"},
        "verdict_status": "status_bearing_non_authoritative",
        "certification_ceiling": "never",
        "validation_status": "pending_validation",
        "is_flux_active": True,
        "authority_level": "extrapolated",
        "valid_range_K": [1400.0, 2200.0],
        "reason": "outside certified SiO source band",
    }
    snapshots = (
        SimpleNamespace(
            c2a_staged_gas={
                "stage_name": "alkali_early_fe",
                "gas_cover_mode": "po2_hold",
            },
            condensed_by_stage_species_delta={},
            evap_flux=SimpleNamespace(carrier_authority_by_species={}),
        ),
        SimpleNamespace(
            c2a_staged_gas={
                "stage_name": "sio_window",
                "gas_cover_mode": "pn2_sweep",
            },
            condensed_by_stage_species_delta={(3, "SiO"): 4.5},
            evap_flux=SimpleNamespace(
                carrier_authority_by_species={"SiO": authority}
            ),
        ),
    )
    return classify_products(SimpleNamespace(
        train=SimpleNamespace(stages=[
            None,
            None,
            None,
            SimpleNamespace(collected_kg={"SiO": 4.5}),
        ]),
        record=SimpleNamespace(snapshots=snapshots),
    ))


def test_present_artifact_renders_route_specific_values_and_authority() -> None:
    metric_label = "source-side O2 potential (emitted; not recovered)"
    artifact = {
        "header": {"charge_mass_kg": 1000.0},
        "timesteps": [
            {
                "hour": 28,
                "summary": {
                    "campaign": "C2B",
                    "pO2_bar": 0.002,
                    "P_total_bar": 0.012,
                    "p_carrier_bar": 0.01,
                    "carrier_identity": "He",
                    "regime": "viscous",
                    "O2_yield_kg_cumulative": 18.0,
                    "O2_metric_label": metric_label,
                    "condensation_train_kg": {"Fe": 0.000005},
                    "metal_yields_kg": {"Fe": 132.0},
                    "vapor_species_kg_hr": {"Fe": 0.02},
                    "wall_deposit_cumulative_kg": {
                        "stage_0_to_stage_1": {"Fe": 0.002}
                    },
                    "metal_phase_stratification": {
                        "status": "diagnostic_only_no_tap_gate",
                        "existing_extraction_behavior": "unchanged_diagnostic_only",
                        "melt_density_fallback_engaged": True,
                        "melt_density_tier": "fallback_basaltic_melt_constant_engine_density_unavailable",
                        "provenance": {
                            "account_state_source": "builtin-authored",
                            "diagnostic_derivation": "builtin-committed"
                        },
                        "carried_mol": {
                            "bottom_pool": {"Fe": 3.5},
                            "float_layer": {"Al": 1.25},
                        },
                        "pool_mol_after": {
                            "bottom_pool": {"Fe": 4.5},
                            "float_layer": {"Al": 2.25},
                        },
                        "pools": {
                            "bottom_pool": {
                                "density_correlation_provenance": {
                                    "Fe": {
                                        "source": "Assael et al. (2006)",
                                        "valid_range_K": [1809.0, 2480.0],
                                        "temperature_K": 1423.15,
                                        "status": "extrapolated_below_valid_range",
                                    }
                                },
                                "buoyancy": {"verdict": "sink"},
                            }
                        },
                    },
                },
                "ledger": {
                    "terminal.oxygen_melt_offgas_stored": {"O2": 2.0},
                    "terminal.oxygen_mre_anode_stored": {"O2": 8.0},
                    "terminal.oxygen_stage0_stored": {"O2": 1.0},
                },
            }
        ],
        "terminal": {"stage_purity": _stage_purity()},
    }

    rendered = _run_panel(artifact)
    html = rendered["html"]

    assert rendered["id"] == "sec-p15-equipment-diagram"
    assert rendered["hasOnTimestep"] is True
    assert 'id="p15-equipment-state" aria-live' not in html
    assert 'id="p15-equipment-status" role="status" aria-live="polite"' in html
    assert "Hour 28 · C2B" in html
    source_o2 = _source_o2(html)
    assert metric_label in source_o2
    assert "18 kg" in source_o2
    assert "4.5 mol" in _tap_card(html, "bottom_pool")
    assert "2.25 mol" in _tap_card(html, "float_layer")
    assert "3.5 mol" not in _tap_card(html, "bottom_pool")
    assert "1.25 mol" not in _tap_card(html, "float_layer")
    assert "Status · diagnostic only no tap gate" in html
    assert "Source · builtin authored" in html
    assert "Derivation · builtin committed" in html
    assert "Behavior · unchanged diagnostic only" in html
    assert "Melt density fallback · engaged" in html
    assert "Melt density tier · fallback basaltic melt constant engine density unavailable" in html
    authority = _authority(html)
    assert "bottom pool Fe density status · extrapolated below valid range" in authority
    assert "bottom pool Fe density source · Assael et al. (2006)" in authority
    assert "bottom pool Fe density valid range · 1,809 K to 2,480 K" in authority
    assert "bottom pool Fe density evaluation temperature · 1,423 K" in authority
    assert "bottom pool buoyancy verdict · sink" in authority
    assert "Bottom-pool diagnostic inventory · no tap gate" in html
    assert "Bottom tray · downward · flow/disposition pending" in html
    assert "Float skim · lateral · flow/disposition pending" in html
    assert 'class="sec-p15-tap-route sec-p15-tap-route--bottom"' in html
    assert 'class="sec-p15-tap-route sec-p15-tap-route--float"' in html
    assert "sec-p15-tap-arrow--down" in html
    assert "sec-p15-tap-arrow--side" in html
    assert 'aria-label="Pot vapor path to condensation train"' in html
    assert 'aria-label="Condensation train path to oxygen separation pump"' in html
    assert "carrier separation / recycle target · recovery not emitted" in html
    assert "CONTAMINATED" in html
    assert "non-designated condensate present" in html
    assert "no material" in _stage_card(html, "stage_0")
    assert "PURE" not in _stage_card(html, "stage_0")
    assert "No classified product mass emitted; verdict is the backend&#39;s empty-stage classification" in html
    assert "No condensate emitted" not in html
    assert "Terminal product-destination classification" in html
    assert "not condenser inventory" in html.lower()
    assert "132 kg" in _readout(html, "metal-product-yields")
    train_readout = _readout(html, "condensation-train-projection")
    assert "Cumulative condensation projection · selected hour" in train_readout
    assert "5.00e-6 kg" in train_readout
    assert "terminal melt-offgas stored O" in train_readout
    assert "not stage-allocated metal-train inventory alone" in train_readout
    assert "Vortex Dust Filter" in html
    assert "Turbine-Compressor" in html
    assert "Turbine Outlet Monitor" in html

    stage_one = _stage_card(html, "stage_1_fe_condenser")
    assert "sec-p15-stage--active" in stage_one
    assert "sec-p15-stage--product" in stage_one
    assert "Designated + coproduct mass" in stage_one
    assert "Accepted mass" not in stage_one
    assert "5.00e-6 kg" in stage_one
    assert "132 kg" not in stage_one
    assert "sec-p15-stage--product" not in _stage_card(html, "stage_3_sio_zone")
    assert "sec-p15-stage--product" not in _stage_card(
        html, "stage_4_alkali_mg_cyclone"
    )

    cryo_before_source_readout = html.split('class="sec-p15-source-o2"', maxsplit=1)[0]
    assert "18 kg" not in cryo_before_source_readout
    assert "2 mol" in _account_segment(
        html, "terminal.oxygen_melt_offgas_stored"
    )
    assert "8 mol" in _account_segment(
        html, "terminal.oxygen_mre_anode_stored"
    )
    assert "1 mol" in _account_segment(html, "terminal.oxygen_stage0_stored")

    vapor = _vapor(html)
    assert "evolved vapor flux" in vapor
    assert "recovered" not in vapor.lower()
    assert "captured" not in vapor.lower()
    assert "product" not in vapor.lower()


def test_sparse_o2_accounts_stay_separate_and_missing_bin_is_pending() -> None:
    artifact = {
        "header": {"charge_mass_kg": 1000.0},
        "timesteps": [
            {
                "hour": 28,
                "summary": {"campaign": "C0"},
                "ledger": {
                    "terminal.oxygen_melt_offgas_stored": {"O2": 2.0},
                    "terminal.oxygen_melt_offgas_vented_to_vacuum": {"O2": 99.0},
                },
            },
            {
                "hour": 79,
                "summary": {"campaign": "C5"},
                "ledger": {
                    "terminal.oxygen_mre_anode_stored": {"O2": 8.0},
                    "terminal.oxygen_melt_offgas_vented_to_vacuum": {"O2": 99.0},
                },
            },
            {
                "hour": 80,
                "summary": {"campaign": "C5"},
                "ledger": {
                    "terminal.oxygen_melt_offgas_stored": {},
                    "terminal.oxygen_mre_anode_stored": {"O2": 0.0},
                },
            },
            {
                # Both known bins positive + a captured sibling: kills per-segment
                # sum mutation and "drop captured from extras" mutation.
                "hour": 81,
                "summary": {"campaign": "C5"},
                "ledger": {
                    "terminal.oxygen_melt_offgas_stored": {"O2": 3.0},
                    "terminal.oxygen_mre_anode_stored": {"O2": 5.0},
                    "terminal.oxygen_mre_anode_captured": {"O2": 1.5},
                },
            },
        ],
        "terminal": {"stage_purity": {}},
    }

    first = _run_panel(artifact, 0)["stateHtml"]
    second = _run_panel(artifact, 1)["stateHtml"]
    partial = _run_panel(artifact, 2)["stateHtml"]
    both_bins = _run_panel(artifact, 3)["stateHtml"]

    assert "2 mol" in _account_segment(
        first, "terminal.oxygen_melt_offgas_stored"
    )
    assert "Not emitted for this hour" in _account_segment(
        first, "terminal.oxygen_mre_anode_stored"
    )
    assert "8 mol" in _account_segment(
        second, "terminal.oxygen_mre_anode_stored"
    )
    assert "Not emitted for this hour" in _account_segment(
        second, "terminal.oxygen_melt_offgas_stored"
    )
    assert "10 mol" not in first + second
    assert "99 mol" not in first + second
    assert "vented_to_vacuum" not in first + second
    assert "total stored" not in (first + second).lower()
    assert "Separate raw-ledger bins · mol · no combined total" in first
    missing_o2 = _account_segment(
        partial, "terminal.oxygen_melt_offgas_stored"
    )
    emitted_zero = _account_segment(
        partial, "terminal.oxygen_mre_anode_stored"
    )
    assert "O₂ not emitted for this account this hour" in missing_o2
    assert "0 mol" not in missing_o2
    assert "0 mol" in emitted_zero
    assert "sec-p15-cryo-segment--pending" not in emitted_zero

    melt_both = _account_segment(both_bins, "terminal.oxygen_melt_offgas_stored")
    mre_both = _account_segment(both_bins, "terminal.oxygen_mre_anode_stored")
    captured = _account_segment(both_bins, "terminal.oxygen_mre_anode_captured")
    assert "3 mol" in melt_both
    assert "5 mol" in mre_both
    assert "1.5 mol" in captured
    assert "8 mol" not in melt_both
    assert "8 mol" not in mre_both
    assert "8 mol" not in captured
    assert "4.5 mol" not in both_bins
    assert "9.5 mol" not in both_bins


def test_negative_and_malformed_stored_o2_are_pending() -> None:
    artifact = {
        "timesteps": [{
            "hour": 1,
            "summary": {"campaign": "C0"},
            "ledger": {
                "terminal.oxygen_melt_offgas_stored": {"O2": -2.0},
                "terminal.oxygen_mre_anode_stored": ["malformed"],
            },
        }],
        "terminal": {"stage_purity": {}},
    }

    html = _run_panel(artifact)["html"]
    negative = _account_segment(
        html, "terminal.oxygen_melt_offgas_stored"
    )
    malformed = _account_segment(
        html, "terminal.oxygen_mre_anode_stored"
    )

    assert "Pending — malformed O₂ quantity emitted" in negative
    assert "-2 mol" not in negative
    assert "sec-p15-cryo-segment--pending" in negative
    assert "Pending — malformed account emitted" in malformed
    assert "sec-p15-cryo-segment--pending" in malformed


def test_partial_inputs_do_not_feed_missing_backend_values() -> None:
    metric_label = "source-side O2 potential (emitted; not recovered)"
    artifact = {
        "header": {
            "charge_mass_kg": 1000.0,
            "effective_config": {"carrier_identity": "He", "p_carrier_bar": 0.9},
        },
        "timesteps": [
            {
                "hour": 12,
                "summary": {
                    "campaign": "C2B",
                    "P_total_bar": 1.2,
                    "pO2_bar": 0.2,
                    # Label emitted, kg absent — label must survive independently.
                    "O2_metric_label": metric_label,
                    "metal_yields_kg": {"Fe": 132.0},
                    "wall_deposit_delta_kg": {
                        "stage_0_to_stage_1": {"Fe": 4.2}
                    },
                    "metal_phase_stratification": {
                        "status": "diagnostic_only_no_tap_gate",
                        "pools": {
                            "bottom_pool": {"species_mol": {"Fe": 7.0}}
                        },
                        "pool_mol_after": {"bottom_pool": {"Fe": 6.0}},
                    },
                },
                "ledger": {
                    "terminal.oxygen_melt_offgas_stored": {"O2": 12.0}
                },
            }
        ],
        "terminal": {
            "stage_purity": {
                "stage_1_fe_condenser": {
                    "label": "Fe Condenser",
                    "designated_species_kg": {"Fe": 3.0},
                    "coproduct_species_kg": {"Ni": 4.0},
                    "impurity_species_kg": {"Si": 2.0},
                    "total_kg": 9.0,
                    "purity_fraction": 0.777777,
                    "verdict": "CONTAMINATED",
                }
            }
        },
    }

    html = _run_panel(artifact)["html"]
    stage_one = _stage_card(html, "stage_1_fe_condenser")
    source_o2 = _source_o2(html)
    train = _readout(html, "condensation-train-projection")
    yields = _readout(html, "metal-product-yields")

    assert "carrier pressure not emitted" in html
    assert "carrier not emitted" in _gas_dome(html)
    assert "He" not in _gas_dome(html)
    assert "Cumulative condensation projection · selected hour" in train
    assert "Pending — not emitted" in train
    assert "132 kg" not in train
    assert "132 kg" in yields
    # Partial O2: preserve emitted label; value stays pending.
    assert metric_label in source_o2
    assert "Cumulative kg value pending — not emitted" in source_o2
    assert "Source-side O₂ readout pending" not in source_o2
    assert "Current melt mass pending" in html
    assert "Per-hour per-stage inventory pending producer emit" in html
    assert stage_one.count("Pending — not emitted") >= 2
    assert "1 bar" not in html
    assert "6 mol" in _tap_card(html, "bottom_pool")
    assert "7 mol" not in _tap_card(html, "bottom_pool")
    assert "7 kg" not in html
    assert "4.2 kg" not in html
    assert "12 kg" not in html
    assert "868 kg" not in html


def test_on_timestep_scrub_updates_hour_campaign_taps_o2_and_coating() -> None:
    artifact = {
        "header": {"charge_mass_kg": 1000.0},
        "timesteps": [
            {
                "hour": 28,
                "summary": {
                    "campaign": "C2B",
                    "metal_phase_stratification": {
                        "pool_mol_after": {
                            "bottom_pool": {"Fe": 1.0},
                            "float_layer": {},
                        }
                    },
                    "wall_deposit_cumulative_kg": {
                        "stage_0_to_stage_1": {"Fe": 0.1}
                    },
                },
                "ledger": {
                    "terminal.oxygen_melt_offgas_stored": {"O2": 2.0}
                },
            },
            {
                "hour": 79,
                "summary": {
                    "campaign": "C4",
                    "metal_phase_stratification": {
                        "pool_mol_after": {
                            "bottom_pool": {},
                            "float_layer": {"Mg": 2.0},
                        }
                    },
                    "wall_deposit_cumulative_kg": {
                        "stage_3_to_stage_4": {"Mg": 0.4}
                    },
                },
                "ledger": {
                    "terminal.oxygen_mre_anode_stored": {"O2": 8.0}
                },
            },
        ],
        "terminal": {"stage_purity": _stage_purity()},
    }

    first = _run_panel(artifact, 0)["stateHtml"]
    second_result = _run_panel(artifact, 1)
    second = second_result["stateHtml"]

    assert "Hour 28 · C2B" in first
    assert "Hour 79 · C4" in second
    assert second_result["statusText"] == "Hour 79 · C4"
    assert "Hour 28 · C2B" not in second
    assert "2 mol" in _tap_card(second, "float_layer")
    target_pipe = _pipe_segment(second, "stage_3_to_stage_4")
    assert "Cumulative wall deposit, stage 3 to 4: Mg 0.4 kg" in target_pipe
    assert "0.4 kg" not in _pipe_segment(second, "stage_2_to_stage_3")
    assert "0.4 kg" not in _pipe_segment(second, "stage_4_to_stage_5")
    assert "8 mol" in _account_segment(
        second, "terminal.oxygen_mre_anode_stored"
    )
    assert "sec-p15-stage--active" in _stage_card(
        second, "stage_4_alkali_mg_cyclone"
    )
    assert "sec-p15-stage--active" not in _stage_card(
        second, "stage_1_fe_condenser"
    )


def test_artifact_values_are_escaped_in_rendered_output() -> None:
    artifact = {
        "header": {"charge_mass_kg": 1000.0},
        "timesteps": [
            {
                "hour": "<img src=x onerror=hour>",
                "summary": {
                    "campaign": "<img src=x onerror=campaign_unique>",
                    "carrier_identity": "<script>alert(1)</script>",
                    "O2_yield_kg_cumulative": 1.0,
                    "O2_metric_label": "source <svg/onload=metric>",
                    "metal_yields_kg": {"<img src=x onerror=species>": 1.0},
                    "metal_phase_stratification": {
                        "status": "diagnostic_only_no_tap_gate",
                        "pools": {
                            "bottom_pool": {
                                "density_correlation_provenance": {
                                    "Fe": {
                                        "source": "<img src=x onerror=density_src>",
                                        "status": "extrapolated",
                                    }
                                },
                                "buoyancy": {"verdict": "sink"},
                            }
                        },
                    },
                },
                "ledger": {
                    "terminal.oxygen_stored<script>": {"O2": 1.0}
                },
            }
        ],
        "terminal": {
            "stage_purity": {
                "stage_1_fe_condenser": {
                    "label": "<img src=x onerror=stage>",
                    "designated_species_kg": {},
                    "coproduct_species_kg": {},
                    "impurity_species_kg": {},
                    "designated_kg": 0.0,
                    "impurity_kg": 0.0,
                    "purity_fraction": 1.0,
                    "verdict": "PURE",
                    "warning": "</p><script>alert(2)</script>",
                }
            }
        },
    }

    html = _run_panel(artifact)["html"]
    selected = _selected_timestep(html)
    warning_stage = _stage_card(html, "stage_1_fe_condenser")
    yields = _readout(html, "metal-product-yields")
    authority = _authority(html)

    assert "<img" not in html
    assert "<script" not in html
    assert "<svg" not in html
    assert "&lt;img" in html
    assert "&lt;script&gt;" in html
    assert "&lt;svg/onload=metric&gt;" in html
    assert "<strong>Hour &lt;img src=x onerror=hour&gt; · &lt;img src=x onerror=campaign_unique&gt;</strong>" in selected
    assert "&amp;lt;img src=x onerror=campaign_unique" not in selected
    assert '<p class="sec-p15-stage-warning">&lt;/p&gt;&lt;script&gt;alert(2)&lt;/script&gt;</p>' in warning_stage
    assert "&amp;lt;" not in warning_stage
    # Species name path: single-encoding only (catches esc(esc(...)) mutation).
    assert "&lt;img src=x onerror=species&gt;" in yields
    assert "&amp;lt;img src=x onerror=species" not in yields
    # textChip sink for density source.
    assert "bottom pool Fe density source · &lt;img src=x onerror=density_src&gt;" in authority
    assert "&amp;lt;img src=x onerror=density_src" not in authority


def test_diagnostic_pool_inventory_and_density_authority_use_emitted_values() -> None:
    artifact = {
        "timesteps": [
            {
                "hour": 32,
                "summary": {
                    "campaign": "C2B",
                    "metal_phase_stratification": {
                        "status": "diagnostic_only_no_tap_gate",
                        "carried_mol": {"bottom_pool": {"Fe": 1.0}},
                        "assigned_mol": {"bottom_pool": {"Fe": 8.0}},
                        "pool_mol_after": {
                            "bottom_pool": {"Fe": 10.0, "Si": 0.264},
                            "float_layer": {"Si": 0.0027},
                        },
                        "si_destination_buoyancy": {
                            "verdict": "BUOYANCY-AMBIGUOUS",
                            "delta_rho_kg_m3": 120.0,
                            "ambiguity_threshold_kg_m3": 268.0,
                            "melt_density_uncertainty_kg_m3": 135.0,
                            "alloy_density_uncertainty_kg_m3": 232.0,
                        },
                        "melt_density_fallback_engaged": True,
                        "melt_density_tier": "fallback_basaltic_melt_constant_engine_density_unavailable",
                        "pools": {
                            "bottom_pool": {
                                "density_correlation_provenance": {
                                    "Fe": {
                                        "source": "Assael et al. (2006), J. Phys. Chem. Ref. Data 35, 285-300, doi:10.1063/1.2149380",
                                        "valid_range_K": [1809.0, 2480.0],
                                        "temperature_K": 1423.15,
                                        "status": "extrapolated_below_valid_range",
                                    }
                                },
                                "buoyancy": {"verdict": "sink"},
                            }
                        },
                    },
                },
                "ledger": {},
            },
            {
                "hour": 33,
                "summary": {
                    "campaign": "C2B",
                    "metal_phase_stratification": {
                        "status": "diagnostic_only_no_tap_gate",
                        "carried_mol": {"bottom_pool": {"Fe": 1.0}},
                        "assigned_mol": {"bottom_pool": {"Fe": 8.0}},
                        "pools": {
                            "bottom_pool": {
                                "density_correlation_provenance": {}
                            }
                        }
                    },
                },
                "ledger": {},
            },
            {
                "hour": 34,
                "summary": {
                    "campaign": "C2B",
                    "metal_phase_stratification": {
                        "status": "diagnostic_only_no_tap_gate",
                        "pool_mol_after": None,
                    },
                },
                "ledger": {},
            },
            {
                "hour": 35,
                "summary": {
                    "campaign": "C2B",
                    "metal_phase_stratification": {
                        "status": "diagnostic_only_no_tap_gate",
                        "pool_mol_after": {"bottom_pool": {}},
                    },
                },
                "ledger": {},
            },
        ],
        "terminal": {"stage_purity": {}},
    }

    html = _run_panel(artifact)["html"]
    bottom_pool = _tap_card(html, "bottom_pool")
    authority = _authority(html)
    partial_pool = _tap_card(_run_panel(artifact, 1)["stateHtml"], "bottom_pool")
    malformed_pool = _tap_card(_run_panel(artifact, 2)["stateHtml"], "bottom_pool")
    missing_sibling_pool = _tap_card(
        _run_panel(artifact, 3)["stateHtml"], "float_layer"
    )

    assert "Bottom-pool diagnostic inventory · no tap gate" in bottom_pool
    assert "10 mol" in bottom_pool
    assert "1 mol" not in bottom_pool
    assert "9 mol" not in bottom_pool
    assert "tap-carried" not in bottom_pool
    assert "Melt density fallback · engaged" in authority
    assert "Melt density tier · fallback basaltic melt constant engine density unavailable" in authority
    assert "bottom pool Fe density status · extrapolated below valid range" in authority
    assert "bottom pool Fe density source · Assael et al. (2006), J. Phys. Chem. Ref. Data 35, 285-300, doi:10.1063/1.2149380" in authority
    assert "bottom pool Fe density valid range · 1,809 K to 2,480 K" in authority
    assert "bottom pool Fe density evaluation temperature · 1,423 K" in authority
    assert "bottom pool buoyancy verdict · sink" in authority
    # Si destination routing uncertainty must survive (not pool-bulk buoyancy).
    assert "Si destination buoyancy verdict · BUOYANCY AMBIGUOUS" in authority
    assert "Si destination Δρ · 120 kg/m³" in authority
    assert "Si destination ambiguity threshold · 268 kg/m³" in authority
    assert "Si destination melt density uncertainty · 135 kg/m³" in authority
    assert "Si destination alloy density uncertainty · 232 kg/m³" in authority
    assert "Pending — not emitted" in partial_pool
    assert "9 mol" not in partial_pool
    assert "Pending — malformed species container emitted" in malformed_pool
    assert "Pending — not emitted" in missing_sibling_pool
    assert "No positive diagnostic pool inventory emitted" not in missing_sibling_pool


def test_product_stripe_requires_positive_matching_emitted_species() -> None:
    def stage_snapshot(species: str) -> dict:
        return {
            "label": "Product stage",
            "designated_species_kg": {species: 1.0},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": 1.0,
            "impurity_kg": 0.0,
            "total_kg": 1.0,
            "purity_fraction": 1.0,
            "verdict": "PURE",
        }

    accepted_cases = [
        ("stage_1_fe_condenser", "Fe", "Designed product route · Fe"),
        ("stage_4_alkali_mg_cyclone", "Na", "Designed product route · Na / K / Mg"),
        ("stage_4_alkali_mg_cyclone", "K", "Designed product route · Na / K / Mg"),
        ("stage_4_alkali_mg_cyclone", "Mg", "Designed product route · Na / K / Mg"),
    ]
    for stage_key, species, route_label in accepted_cases:
        stages = _stage_purity()
        stages[stage_key] = stage_snapshot(species)
        artifact = {
            "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
            "terminal": {"stage_purity": stages},
        }
        stage = _stage_card(_run_panel(artifact)["html"], stage_key)

        assert "sec-p15-stage--product" in stage
        assert route_label in stage
        assert "terminal designated mass emitted" in stage
        assert "collection not implied" not in stage

    for species in ("SiO", "SiO2"):
        stages = _stage_purity()
        stages["stage_3_sio_zone"] = stage_snapshot(species)
        artifact = {
            "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
            "terminal": {
                "stage_purity": stages,
                "product_classification": {
                    "pure_silica_glass": {
                        "stage_3_capture_kg": 1.0,
                        "stage_3_kg_by_species": {species: 1.0},
                        "class_total_kg": 1.0,
                    }
                },
            },
        }
        stage = _stage_card(_run_panel(artifact)["html"], "stage_3_sio_zone")

        assert "sec-p15-stage--product" in stage
        assert "Designed product route · SiO / SiO₂" in stage
        assert "qualified silica product" in stage
        assert "collection not implied" not in stage
        assert "flagged capture" not in stage

    foreign_stages = _stage_purity()
    foreign_stages["stage_3_sio_zone"] = stage_snapshot("Fe")
    foreign_artifact = {
        "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
        "terminal": {"stage_purity": foreign_stages},
    }
    foreign_stage = _stage_card(
        _run_panel(foreign_artifact)["html"], "stage_3_sio_zone"
    )
    empty_alkali_stage = _stage_card(
        _run_panel({
            "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
            "terminal": {"stage_purity": _stage_purity()},
        })["html"],
        "stage_4_alkali_mg_cyclone",
    )

    assert "sec-p15-stage--product" not in foreign_stage
    assert "Designed product route · SiO / SiO₂ · collection not implied" in foreign_stage
    assert "sec-p15-stage--product" not in empty_alkali_stage
    assert "Designed product route · Na / K / Mg · collection not implied" in empty_alkali_stage

    # Zero / negative matching product species must not paint product stripe
    # (mutation: drop `&& designated[species] > 0`).
    for bad_amount in (0.0, -1.0):
        zero_stages = _stage_purity()
        zero_stages["stage_4_alkali_mg_cyclone"] = {
            "label": "Alkali/Mg Cyclone",
            "designated_species_kg": {"Na": bad_amount},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": bad_amount,
            "impurity_kg": 0.0,
            "total_kg": abs(bad_amount),
            "purity_fraction": 1.0,
            "verdict": "PURE",
        }
        zero_stage = _stage_card(
            _run_panel({
                "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
                "terminal": {"stage_purity": zero_stages},
            })["html"],
            "stage_4_alkali_mg_cyclone",
        )
        assert "sec-p15-stage--product" not in zero_stage
        assert "terminal designated mass emitted" not in zero_stage
        assert "collection not implied" in zero_stage
        assert "--sec-p15-product" not in zero_stage


def test_p15_unqualified_silica_capture_is_flagged_not_product_glow() -> None:
    stages = _stage_purity()
    stages["stage_3_sio_zone"] = {
        "label": "SiO Zone",
        "designated_species_kg": {"SiO": 4.0},
        "coproduct_species_kg": {},
        "impurity_species_kg": {},
        "designated_kg": 4.0,
        "impurity_kg": 0.0,
        "total_kg": 4.0,
        "purity_fraction": 1.0,
        "verdict": "PURE",
    }
    artifact = {
        "timesteps": [{"hour": 1, "summary": {"campaign": "C2A"}, "ledger": {}}],
        "terminal": {
            "stage_purity": stages,
            "product_classification": {
                "pure_silica_glass": {
                    "stage_3_capture_kg": 4.0,
                    "stage_3_kg_by_species": {"SiO": 4.0},
                    "class_total_kg": 0.0,
                }
            },
        },
    }
    stage = _stage_card(_run_panel(artifact)["html"], "stage_3_sio_zone")

    assert "sec-p15-stage--product" not in stage
    assert "--sec-p15-product" not in stage
    assert "qualified silica product" not in stage
    assert "terminal designated mass emitted" not in stage
    assert "flagged capture · not a product" in stage

    ungated = _stage_card(
        _run_panel({
            "timesteps": [{"hour": 1, "summary": {"campaign": "C2A"}, "ledger": {}}],
            "terminal": {"stage_purity": stages},
        })["html"],
        "stage_3_sio_zone",
    )
    assert "sec-p15-stage--product" not in ungated
    assert "qualified silica product" not in ungated
    assert "flagged capture" not in ungated


def test_p15_flagged_silica_product_keeps_product_route_and_flag() -> None:
    stages = _stage_purity()
    stages["stage_3_sio_zone"] = {
        "label": "SiO Zone",
        "designated_species_kg": {"SiO": 4.5},
        "coproduct_species_kg": {},
        "impurity_species_kg": {},
        "designated_kg": 4.5,
        "impurity_kg": 0.0,
        "total_kg": 4.5,
        "purity_fraction": 1.0,
        "verdict": "PURE",
    }
    classification = _flagged_silica_product_classification()
    silica = classification["pure_silica_glass"]
    artifact = {
        "timesteps": [{"hour": 1, "summary": {"campaign": "C2A"}, "ledger": {}}],
        "terminal": {
            "stage_purity": stages,
            "product_classification": classification,
        },
    }

    stage = _stage_card(_run_panel(artifact)["html"], "stage_3_sio_zone")

    assert silica["class_total_kg"] == 4.5
    assert silica["flag"]["authority"] == "extrapolated"
    assert "sec-p15-stage--product" in stage
    assert "flagged silica product" in stage
    assert "qualified silica product" not in stage
    assert "flagged capture · not a product" not in stage
    assert "status: flagged prediction" in stage
    assert "authority: extrapolated" in stage
    assert "band: [1400,2200]" in stage
    assert "reason: outside certified SiO source band" in stage


def test_empty_stage_indeterminate_renders_as_no_material() -> None:
    stage = _stage_card(
        _run_panel({
            "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
            "terminal": {"stage_purity": _stage_purity()},
        })["html"],
        "stage_0",
    )

    assert "no material" in stage
    assert "PURE" not in stage
    assert "0%" not in stage
    assert ">1<" not in stage
    assert "Emitted purity fraction</span><strong>no material</strong>" in stage


def test_species_presence_visuals_distinguish_absent_empty_zero_and_malformed() -> None:
    artifact = {
        "timesteps": [
            {"hour": 0, "summary": {"campaign": "C0"}, "ledger": {}},
            {
                "hour": 1,
                "summary": {
                    "campaign": "C0",
                    "wall_deposit_cumulative_kg": {},
                    "vapor_species_kg_hr": {},
                },
                "ledger": {},
            },
            {
                "hour": 2,
                "summary": {
                    "campaign": "C0",
                    "wall_deposit_cumulative_kg": {
                        "stage_0_to_stage_1": {"Fe": 0.0}
                    },
                    "vapor_species_kg_hr": {"Fe": 0.0},
                },
                "ledger": {},
            },
            {
                "hour": 3,
                "summary": {
                    "campaign": "C0",
                    "wall_deposit_cumulative_kg": {
                        "stage_0_to_stage_1": {"Fe": -1.0}
                    },
                    "vapor_species_kg_hr": {"Fe": -1.0},
                    "metal_yields_kg": None,
                },
                "ledger": {},
            },
            {
                "hour": 4,
                "summary": {
                    "campaign": "C0",
                    "wall_deposit_cumulative_kg": {
                        "stage_0_to_stage_1": {"Fe": "1.0"}
                    },
                    "vapor_species_kg_hr": {"Fe": "1.0"},
                    "metal_yields_kg": {"Fe": "1.0"},
                },
                "ledger": {},
            },
            {
                # speciesList zero branch for product / train / tap maps.
                "hour": 5,
                "summary": {
                    "campaign": "C0",
                    "metal_yields_kg": {"Fe": 0.0},
                    "condensation_train_kg": {"Fe": 0.0},
                    "vapor_species_kg_hr": {"Fe": 0.02},
                    "metal_phase_stratification": {
                        "pool_mol_after": {"bottom_pool": {"Fe": 0.0}},
                    },
                },
                "ledger": {},
            },
        ],
        "terminal": {"stage_purity": {}},
    }

    absent = _run_panel(artifact, 0)["stateHtml"]
    empty = _run_panel(artifact, 1)["stateHtml"]
    zero = _run_panel(artifact, 2)["stateHtml"]
    malformed = _run_panel(artifact, 3)["stateHtml"]
    nonnumeric = _run_panel(artifact, 4)["stateHtml"]
    species_list_zero = _run_panel(artifact, 5)["stateHtml"]

    assert "Cumulative wall deposit not emitted for stage 0 to 1" in _pipe_segment(
        absent, "stage_0_to_stage_1"
    )
    assert "Vapor flux not emitted" in _vapor(absent)
    assert "Pending — not emitted" in _readout(absent, "metal-product-yields")
    assert "Terminal product-destination classification pending — not emitted" in _stage_card(
        absent, "stage_1_fe_condenser"
    )
    assert "0 kg" not in _stage_card(absent, "stage_1_fe_condenser")
    assert "No non-negligible cumulative wall deposit recorded for stage 0 to 1" in _pipe_segment(
        empty, "stage_0_to_stage_1"
    )
    assert "No non-negligible vapor species recorded this hour" in _vapor(empty)
    assert "Cumulative wall deposit emitted as zero for stage 0 to 1" in _pipe_segment(
        zero, "stage_0_to_stage_1"
    )
    assert "--sec-p15-species" not in _pipe_segment(zero, "stage_0_to_stage_1")
    assert "Vapor species emitted as zero this hour" in _vapor(zero)
    assert "evolved vapor flux" not in _vapor(zero)
    assert "Cumulative wall deposit malformed for stage 0 to 1" in _pipe_segment(
        malformed, "stage_0_to_stage_1"
    )
    assert "--sec-p15-species" not in _pipe_segment(
        malformed, "stage_0_to_stage_1"
    )
    assert "Vapor flux malformed" in _vapor(malformed)
    assert "evolved vapor flux" not in _vapor(malformed)
    assert "Pending — malformed species container emitted" in _readout(
        malformed, "metal-product-yields"
    )
    assert "Cumulative wall deposit malformed for stage 0 to 1" in _pipe_segment(
        nonnumeric, "stage_0_to_stage_1"
    )
    assert "Vapor flux malformed" in _vapor(nonnumeric)
    assert "Pending — malformed species quantity emitted" in _readout(
        nonnumeric, "metal-product-yields"
    )

    yields_zero = _readout(species_list_zero, "metal-product-yields")
    train_zero = _readout(species_list_zero, "condensation-train-projection")
    tap_zero = _tap_card(species_list_zero, "bottom_pool")
    vapor_positive = _vapor(species_list_zero)
    assert "Emitted zero species quantity" in yields_zero
    assert "Emitted zero species quantity" in train_zero
    assert "Emitted zero species quantity" in tap_zero
    assert "--sec-p15-species" not in yields_zero
    assert "--sec-p15-species" not in train_zero
    assert "--sec-p15-species" not in tap_zero
    assert "evolved vapor flux" in vapor_positive
    assert "kg/h" in vapor_positive
    assert "recovered" not in vapor_positive.lower()


def test_missing_purity_is_not_derived_from_emitted_stage_masses() -> None:
    artifact = {
        "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
        "terminal": {
            "stage_purity": {
                "stage_1_fe_condenser": {
                    "label": "Fe Condenser",
                    "designated_species_kg": {"Fe": 7.0},
                    "coproduct_species_kg": {},
                    "impurity_species_kg": {"Si": 2.0},
                    "designated_kg": 7.0,
                    "impurity_kg": 2.0,
                    "total_kg": 9.0,
                    "verdict": "CONTAMINATED",
                }
            }
        },
    }

    stage = _stage_card(_run_panel(artifact)["html"], "stage_1_fe_condenser")

    assert "<span>Emitted purity fraction</span><strong>Pending — not emitted</strong>" in stage
    assert "0.7778" not in stage


def test_product_yield_readout_uses_emitter_semantics() -> None:
    artifact = {
        "timesteps": [{
            "hour": 1,
            "summary": {"campaign": "C0", "metal_yields_kg": {"Fe": 132.0}},
            "ledger": {},
        }],
        "terminal": {"stage_purity": {}},
    }

    readout = _readout(_run_panel(artifact)["html"], "metal-product-yields")

    assert "Metal product yields · cumulative product-ledger projection" in readout
    assert "132 kg" in readout
    assert "Route-wide product readout only; never used as a condenser fill" in readout
    assert "not recovered product" not in readout


def test_unmapped_campaign_is_explicit_and_does_not_glow_a_stage() -> None:
    artifact = {
        "timesteps": [{"hour": 79, "summary": {"campaign": "C5"}, "ledger": {}}],
        "terminal": {"stage_purity": _stage_purity()},
    }

    html = _run_panel(artifact)["html"]

    assert "Campaign-to-stage map not emitted for C5; no stage glow inferred" in _selected_timestep(html)
    assert "sec-p15-stage--active" not in html


def test_c3_campaigns_cue_product_destinations_not_alkali_cyclone() -> None:
    # C3_K → Fe stage 1 only; C3_NA → Fe stage 1 + Cr stage 2 + Ti metal-phase.
    # Mutation: put C3_K/C3_NA back on stage_4 campaigns list.
    c3k = _run_panel({
        "timesteps": [{"hour": 10, "summary": {"campaign": "C3_K"}, "ledger": {}}],
        "terminal": {"stage_purity": _stage_purity()},
    })["html"]
    c3na = _run_panel({
        "timesteps": [{"hour": 11, "summary": {"campaign": "C3_NA"}, "ledger": {}}],
        "terminal": {"stage_purity": _stage_purity()},
    })["html"]

    assert "sec-p15-stage--active" in _stage_card(c3k, "stage_1_fe_condenser")
    assert "sec-p15-stage--active" not in _stage_card(c3k, "stage_4_alkali_mg_cyclone")
    assert "Designed campaign-route cue: Fe Condenser" in _selected_timestep(c3k)
    assert "Alkali/Mg Cyclone" not in _selected_timestep(c3k)

    assert "sec-p15-stage--active" in _stage_card(c3na, "stage_1_fe_condenser")
    assert "sec-p15-stage--active" in _stage_card(c3na, "stage_2_cr_oxide_harvest")
    assert "sec-p15-stage--active" not in _stage_card(c3na, "stage_4_alkali_mg_cyclone")
    selected_na = _selected_timestep(c3na)
    assert "Fe Condenser" in selected_na
    assert "Cr Oxide Harvester" in selected_na
    assert "Ti metal-phase (non-condenser)" in selected_na
    assert "Alkali/Mg Cyclone" not in selected_na


def test_condensation_train_projection_discloses_melt_offgas_o2() -> None:
    # Relabel: do not claim pure metal-train inventory when O2 is co-emitted.
    artifact = {
        "timesteps": [{
            "hour": 1,
            "summary": {
                "campaign": "C0",
                "condensation_train_kg": {"Fe": 0.000005, "O2": 3.1998},
            },
            "ledger": {
                "terminal.oxygen_melt_offgas_stored": {"O2": 100.0},
            },
        }],
        "terminal": {"stage_purity": {}},
    }
    html = _run_panel(artifact)["html"]
    train = _readout(html, "condensation-train-projection")
    cryo = _account_segment(html, "terminal.oxygen_melt_offgas_stored")

    assert "Cumulative condensation projection · selected hour" in train
    assert "3.2 kg" in train or "3.1998 kg" in train
    assert "5.00e-6 kg" in train
    assert "terminal melt-offgas stored O" in train
    assert "metal-train inventory alone" in train
    assert "Cumulative train species inventory" not in train
    assert "100 mol" in cryo


def test_malformed_stage_scalars_are_pending_not_measured() -> None:
    # Mutation: accept negative mass / purity>1 as ordinary metrics.
    artifact = {
        "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
        "terminal": {
            "stage_purity": {
                "stage_1_fe_condenser": {
                    "label": "Fe Condenser",
                    "designated_species_kg": {"Fe": -2.0},
                    "coproduct_species_kg": {},
                    "impurity_species_kg": {},
                    "designated_kg": -2.0,
                    "impurity_kg": 0.0,
                    "total_kg": -2.0,
                    "purity_fraction": 1.5,
                    "verdict": "PURE",
                }
            }
        },
    }
    stage = _stage_card(_run_panel(artifact)["html"], "stage_1_fe_condenser")

    assert "Pending — malformed quantity emitted" in stage
    assert stage.count("Pending — malformed quantity emitted") >= 2
    assert "-2 kg" not in stage
    assert ">1.5<" not in stage and ">1.50<" not in stage
    assert "<strong>1.5</strong>" not in stage
    assert "Pending — malformed species quantity emitted" in stage


def test_empty_stage_without_verdict_does_not_invent_backend_classification() -> None:
    # Mutation: always claim "backend's empty-stage classification" when total_kg==0.
    artifact = {
        "timesteps": [{"hour": 1, "summary": {"campaign": "C0"}, "ledger": {}}],
        "terminal": {
            "stage_purity": {
                "stage_0": {
                    "label": "Hot Duct",
                    "designated_species_kg": {},
                    "coproduct_species_kg": {},
                    "impurity_species_kg": {},
                    "designated_kg": 0.0,
                    "impurity_kg": 0.0,
                    "total_kg": 0.0,
                    "purity_fraction": 1.0,
                }
            }
        },
    }
    stage = _stage_card(_run_panel(artifact)["html"], "stage_0")

    assert "verdict pending — not emitted" in stage
    assert "backend&#39;s empty-stage classification" not in stage
    assert "backend's empty-stage classification" not in stage
    assert "PENDING" in stage


def test_p15_stage_purity_mass_is_classification_not_condenser_inventory() -> None:
    """stage_purity kilograms include tap metal; they are not vessel fill.

    Target-sample counterexample: stage_1 designated Fe 85.22 kg, bottom-pool
    Fe 1526 mol, aggregate train Fe 0.027 kg. Mutation: labeling that 85 kg
    as condenser contents / condensate inventory would fail.
    """
    artifact = {
        "header": {"charge_mass_kg": 1000.0},
        "timesteps": [
            {
                "hour": 79,
                "summary": {
                    "campaign": "C2B",
                    "condensation_train_kg": {"Fe": 0.02739867051993343},
                    "metal_phase_stratification": {
                        "pool_mol_after": {
                            "bottom_pool": {"Fe": 1525.994711609524},
                        }
                    },
                },
                "ledger": {},
            }
        ],
        "terminal": {
            "stage_purity": {
                "stage_1_fe_condenser": {
                    "label": "Fe Condenser",
                    "designated_species_kg": {"Fe": 85.22329294683593},
                    "coproduct_species_kg": {},
                    "impurity_species_kg": {},
                    "designated_kg": 85.22329294683593,
                    "impurity_kg": 0.0,
                    "total_kg": 85.22329294683593,
                    "purity_fraction": 1.0,
                    "verdict": "PURE",
                }
            },
            "final_state": {
                "process.metal_phase_bottom_pool": {"Fe": 1525.994711609524},
            },
        },
    }
    result = _run_panel(artifact)
    html = result["html"]
    stage = _stage_card(html, "stage_1_fe_condenser")
    train = _readout(html, "condensation-train-projection")

    assert "85.22 kg" in stage
    assert "Designated + coproduct mass" in stage
    assert "terminal product-destination classification" in stage.lower()
    assert "not condenser inventory" in stage.lower()
    assert "condenser contents" not in html.lower()
    assert "No condensate emitted" not in html
    assert "not vessel fill" in html.lower()
    assert "not condenser inventory" in html.lower()
    assert "stage-routed tap metal" in html.lower()
    assert "Per-hour per-stage inventory pending producer emit" in html
    assert "0.0274 kg" in train
    assert "85.22 kg" not in train
    assert "85.22 kg" not in _tap_card(html, "bottom_pool")
