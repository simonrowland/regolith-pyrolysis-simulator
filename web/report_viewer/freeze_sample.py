#!/usr/bin/env python3
"""Freeze a real runner payload into the W-A0 run-artifact envelope (ARTIFACT-SCHEMA.md).

This is a DEMO-sample generator for the engine-free report viewer (Phase 2 / t-236). It repackages an
existing static runner payload (scratchpad/fullseq_lunar.json — a real 197h C0->C6 lunar run) into the
frozen artifact shape `{artifact_schema_version, execution_status, lifecycle, header, timesteps[], terminal}`.

It does NOT run any engine — pure repackaging (the mandate's W-A0 job). The NEW backend-owned fields
(yield_disposition, summary.p_non_O2_bar, stage_purity.activity, terminal.wall_lifetime) are deliberately
LEFT ABSENT — the runner doesn't emit them yet — so the viewer's honest "pending" path is exercised.

Usage: python3 freeze_sample.py <payload.json> <out.json>
"""
import json
import sys

ARTIFACT_SCHEMA_VERSION = "0.1.0"

# Owner-ratified two-price energy model (T-7, 2026-07-15) — injected into the demo header so the settings
# inspector has a cost_block to render. In production W-A5a emits this; here it's demo defaults.
DEMO_COST_BLOCK = {
    "electrical_cost_per_kWh": 10.00,   # for energy_electrical_kWh
    "solar_heat_cost_per_kWh": 0.05,    # for evaporation_thermal + latent + dissociation
    "_provenance": "demo defaults (owner T-7); production values from W-A5a",
}


def _campaign_chain(per_hour):
    seen, chain = set(), []
    for row in per_hour:
        c = row.get("campaign")
        if c and c not in seen:
            seen.add(c)
            chain.append(c)
    return chain


def reshape(payload):
    rm = payload.get("run_metadata", {}) or {}
    per_hour = payload.get("per_hour_summary", []) or []

    status = payload.get("status", "ok")  # runner emits ok|partial|refused|failed
    artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "execution_status": status,
        "lifecycle": "complete",  # a captured run, not cancelled
    }
    if status != "ok":
        artifact["failure"] = {
            "reason": payload.get("reason"),
            "error_message": payload.get("error_message"),
        }

    artifact["header"] = {
        "run_id": "sample-fullseq-lunar-197h",
        "name": "Full-sequence lunar (C0->C6, demo)",
        "created_at": rm.get("started_at_utc"),
        "seed": rm.get("seed"),
        "feedstock_id": rm.get("feedstock_id"),
        "charge_mass_kg": rm.get("mass_kg"),
        "c3_dose": rm.get("c3_alkali_credit_dose_kg_by_species"),
        "campaign_chain": _campaign_chain(per_hour),
        "engine_identity": {
            "name": rm.get("backend"),
            "cache_version": rm.get("kernel_commit_sha"),
            "backend_wire_token": rm.get("backend"),
        },
        # recipe_snapshot: the payload carries no importable recipe; a real run would (W-A4). Minimal here.
        "recipe_snapshot": {"note": "not captured in this payload; W-A4 emits the importable recipe"},
        "target_snapshot": None,
        # effective_config (W-A5) absent -> settings inspector shows what it has
        "cost_block": DEMO_COST_BLOCK,  # demo two-price model (production: W-A5a)
    }

    # timesteps: each per_hour row IS summary (verbatim). ledger/condenser/turbine are W-A3/optional -> absent.
    artifact["timesteps"] = [
        {"hour": row.get("hour"), "summary": row}
        for row in per_hour
    ]

    artifact["terminal"] = {
        # products/taps/rump are reader-derived from final_state (the terminal ledger accounts); pass through
        # the raw accounts so the viewer can render composition without a parallel store.
        "final_state": payload.get("final_state", {}),
        "final": payload.get("final", {}),
        "stage_purity": payload.get("stage_purity_report", {}),
        "vapor_pressure_source_report": payload.get("vapor_pressure_source_report", {}),
        "run_metadata": rm,
        "mass_balance_closure": {
            "residual_pct": (per_hour[-1].get("mass_balance_pct") if per_hour else None),
            "basis": "final per-hour mass_balance_pct",
        },
        # NEW backend-owned fields deliberately ABSENT (viewer -> "pending"): yield_disposition, wall_lifetime,
        # terminal_product_taxonomy, confidence_inputs.
    }
    return artifact


def main():
    src, out = sys.argv[1], sys.argv[2]
    with open(src) as f:
        payload = json.load(f)
    artifact = reshape(payload)
    with open(out, "w") as f:
        json.dump(artifact, f, indent=1)
    # brief report
    print(f"wrote {out}")
    print(f"  execution_status={artifact['execution_status']} lifecycle={artifact['lifecycle']}")
    print(f"  timesteps={len(artifact['timesteps'])}")
    print(f"  header.feedstock_id={artifact['header']['feedstock_id']} charge_mass_kg={artifact['header']['charge_mass_kg']}")
    print(f"  campaign_chain={artifact['header']['campaign_chain']}")
    print(f"  terminal.stage_purity stages={list(artifact['terminal']['stage_purity'].keys())}")
    print(f"  terminal.final_state accounts={len(artifact['terminal']['final_state'])}")


if __name__ == "__main__":
    main()
