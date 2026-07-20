from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess


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
            "purity_fraction": 1.0,
            "verdict": "PURE",
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
                        "provenance": {
                            "account_state_source": "builtin-authored",
                            "diagnostic_derivation": "builtin-committed"
                        },
                        "carried_mol": {
                            "bottom_pool": {"Fe": 3.5},
                            "float_layer": {"Al": 1.25},
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
    assert metric_label in html
    assert "18 kg" in html
    assert "3.5 mol" in html
    assert "1.25 mol" in html
    assert "Status · diagnostic only no tap gate" in html
    assert "Source · builtin authored" in html
    assert "Derivation · builtin committed" in html
    assert "Behavior · unchanged diagnostic only" in html
    assert "Bottom-pool tap view · diagnostic carried mol" in html
    assert "carrier separation / recycle target · recovery not emitted" in html
    assert "CONTAMINATED" in html
    assert "non-designated condensate present" in html
    assert "No condensate emitted; verdict is the backend&#39;s empty-stage classification" in html
    assert "132 kg" in html
    assert "Vortex Dust Filter" in html
    assert "Turbine-Compressor" in html
    assert "Turbine Outlet Monitor" in html

    stage_one = _stage_card(html, "stage_1_fe_condenser")
    assert "sec-p15-stage--active" in stage_one
    assert "sec-p15-stage--product" in stage_one
    assert "5.00e-6 kg" in stage_one
    assert "132 kg" not in stage_one
    assert "sec-p15-stage--product" in _stage_card(html, "stage_3_sio_zone")
    assert "sec-p15-stage--product" in _stage_card(
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


def test_sparse_o2_accounts_stay_separate_and_missing_bin_is_pending() -> None:
    artifact = {
        "header": {"charge_mass_kg": 1000.0},
        "timesteps": [
            {
                "hour": 28,
                "summary": {"campaign": "C0"},
                "ledger": {
                    "terminal.oxygen_melt_offgas_stored": {"O2": 2.0}
                },
            },
            {
                "hour": 79,
                "summary": {"campaign": "C5"},
                "ledger": {
                    "terminal.oxygen_mre_anode_stored": {"O2": 8.0}
                },
            },
        ],
        "terminal": {"stage_purity": {}},
    }

    first = _run_panel(artifact, 0)["stateHtml"]
    second = _run_panel(artifact, 1)["stateHtml"]

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
    assert "total stored" not in (first + second).lower()
    assert "Separate raw-ledger bins · mol · no combined total" in first


def test_partial_inputs_do_not_feed_missing_backend_values() -> None:
    artifact = {
        "header": {"charge_mass_kg": 1000.0},
        "timesteps": [
            {
                "hour": 12,
                "summary": {
                    "campaign": "C2B",
                    "P_total_bar": 1.2,
                    "pO2_bar": 0.2,
                    "carrier_identity": "He",
                    "O2_metric_label": "source-side potential (not recovered)",
                    "metal_yields_kg": {"Fe": 132.0},
                    "wall_deposit_delta_kg": {
                        "stage_0_to_stage_1": {"Fe": 4.2}
                    },
                    "metal_phase_stratification": {
                        "status": "diagnostic_only_no_tap_gate",
                        "pools": {
                            "bottom_pool": {"species_mol": {"Fe": 7.0}}
                        },
                        "pool_mol_after": {"bottom_pool": {"Fe": 7.0}},
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

    assert "carrier pressure not emitted" in html
    assert "Cumulative train species inventory · selected hour" in html
    assert "Source-side O₂ readout pending" in html
    assert "Current melt mass pending" in html
    assert "Per-hour per-stage inventory pending producer emit" in html
    assert stage_one.count("Pending — not emitted") >= 2
    assert "1 bar" not in html
    assert "7 mol" not in html
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
                        "carried_mol": {
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
                        "carried_mol": {
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
    assert "2 mol" in second
    assert "0.4 kg" in second
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
                    "campaign": "<img src=x onerror=campaign>",
                    "carrier_identity": "<script>alert(1)</script>",
                    "O2_yield_kg_cumulative": 1.0,
                    "O2_metric_label": "source <svg/onload=metric>",
                    "metal_yields_kg": {"<img src=x onerror=species>": 1.0},
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

    assert "<img" not in html
    assert "<script" not in html
    assert "<svg" not in html
    assert "&lt;img" in html
    assert "&lt;script&gt;" in html
    assert "&lt;svg/onload=metric&gt;" in html
