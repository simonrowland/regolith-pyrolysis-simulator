#!/usr/bin/env python3
"""Emit code-embedded pin values under the current engine config.

Machine-sensitive. Intended for scripts/studio-regen.sh on mac-studio-256-1
under the CI grind engines.local.toml. Writes a JSON value report the harness
ships back so the local worker can patch pins with doctrine comments.

Probes match the divergent train11 pin tests exactly:
  - capacity_coupling head-result trio (default-off hot Fe redox split)
  - sio chain wall-T-invariant evolved SiO
  - sio step Stage-3 silica + cold-liner wall deposit
  - staged_bakeout Stage-3 silica + product SiO after C2A_staged advance 30
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.studio_pin_support import (  # noqa: E402
    capacity_pin_values, sio_pin_values, sio_report_at_wall_temperature,
    sio_evolved_baseline, staged_pin_values, sio_wall_report_at_temperature,
)

REPORT_PATH = ROOT / "studio-pin-report.json"

# Laptop-config (committed) baselines for honesty-gate comparison in the report.
# Values are the pre-regen pins at tip 0de9c6d / train11 expected side.
LAPTOP_BASELINES: dict[str, float] = {
    "capacity_total_kg_hr": 2.6213753068336443,
    "capacity_transport_saturation_pct": 1161978.521915791,
    "capacity_melt_mass_kg": 997.3707383784229,
    "sio_evolved_kg": 1.03187282595e-05,
    "sio_stage3_silica_kg": 6.73119341581e-06,
    "sio_wall_deposit_1050_kg": 4.439481519259e-06,
    "staged_silica_kg": 0.10262754045817979,
    "staged_product_sio_kg": 0.011456288948428558,
}


def _emit_capacity() -> dict:
    from simulator.state import CampaignPhase
    from tests.test_capacity_coupling import _real_capacity_sim

    sim = _real_capacity_sim()
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    # Match test_default_off_preserves_hot_fe_redox_split_head_result: default-off
    # live path must not compute capacity shadow.
    sim._compute_capacity_coupling_shadow = lambda _eq: (_ for _ in ()).throw(
        AssertionError("default-off live path must not compute capacity")
    )
    snapshot = sim.step()
    return {
        **capacity_pin_values(snapshot),
        "n_transitions": len(sim.atom_ledger.transitions),
        "last5_reasons": [
            t.reason for t in sim.atom_ledger.transitions[-5:]
        ],
        "mass_balance_error_pct": float(snapshot.mass_balance_error_pct),
    }


def _emit_sio() -> dict:
    report_at = sio_report_at_wall_temperature

    evolved = []
    for liner in (1050.0, 1300.0, 1400.0, 1500.0):
        report, diagnostics = report_at(liner)
        evolved.append(sio_pin_values(report)["sio_evolved_kg"])
        assert abs(float(diagnostics["mass_balance_error_pct"])) <= 5e-12

    report_1400, _ = report_at(1400.0)
    pins_1400 = sio_pin_values(report_1400)
    # Match the wall-step test's explicit fallback probe, independently of
    # the live-engine evolved-yield and designated-stage capture above.
    wall_pins = {
        temperature: sio_pin_values(sio_wall_report_at_temperature(temperature)[0])
        for temperature in (1050.0, 1400.0, 1500.0)
    }

    # Wall-T invariance of evolved SiO is a physics contract.
    spread = max(evolved) - min(evolved)
    return {
        "sio_evolved_kg_by_wall_T": {
            "1050": evolved[0],
            "1300": evolved[1],
            "1400": evolved[2],
            "1500": evolved[3],
        },
        "sio_evolved_kg": evolved[0],
        "sio_evolved_kg_by_feedstock": {
            "lunar_mare_low_ti": evolved[0],
            "mars_basalt": sio_pin_values(sio_report_at_wall_temperature(1400.0, feedstock="mars_basalt")[0])["sio_evolved_kg"],
        },
        "committed_sio_evolved_kg_by_feedstock": {
            feedstock: sio_evolved_baseline(feedstock)
            for feedstock in ("lunar_mare_low_ti", "mars_basalt")
        },
        "sio_evolved_wall_T_spread_kg": spread,
        "sio_stage3_silica_kg": pins_1400["sio_stage3_silica_kg"],
        "sio_wall_deposit_1050_kg": wall_pins[1050.0]["sio_wall_deposit_kg"],
        "sio_wall_deposit_1400_kg": wall_pins[1400.0]["sio_wall_deposit_kg"],
        "sio_wall_deposit_1500_kg": wall_pins[1500.0]["sio_wall_deposit_kg"],
        "wall_SiO_kg_1050": wall_pins[1050.0]["wall_SiO_kg"],
        "wall_probe": "unavailable_magemin_fallback_test_only_not_real_engine_evidence",
    }


def _emit_staged() -> dict:
    from tests.test_staged_bakeout import _run_staged

    sim = _run_staged()
    products = sim.product_ledger()
    sio_stage = sim.train.stages[3].collected_kg
    return {
        **staged_pin_values(sim),
        "staged_sio_stage_keys": sorted(sio_stage.keys()),
        "staged_sio_stage_Fe_kg": float(sio_stage.get("Fe", 0.0)),
        "staged_products_Na_kg": float(products.get("Na", 0.0)),
        "staged_products_Fe_kg": float(products.get("Fe", 0.0)),
    }


def _honesty_rows(values: dict[str, float]) -> list[dict]:
    rows = []
    for key, old in LAPTOP_BASELINES.items():
        if key not in values:
            continue
        new = float(values[key])
        delta = new - old
        rel = (delta / old) if old != 0.0 else (float("inf") if delta else 0.0)
        # Wild magnitude: >50% relative on a physics yield/mass pin, or absolute
        # sign flip on a strictly-positive yield. Capacity saturation can be large
        # absolute but is a diagnostic ratio — flag only if total_kg_hr goes wild.
        wild = abs(rel) > 0.5 and key not in {
            "capacity_transport_saturation_pct",
        }
        if key == "capacity_total_kg_hr" and abs(rel) > 0.25:
            wild = True
        if key in {
            "sio_evolved_kg",
            "sio_stage3_silica_kg",
            "staged_silica_kg",
            "staged_product_sio_kg",
        } and new < 0:
            wild = True
        rows.append(
            {
                "key": key,
                "laptop": old,
                "studio": new,
                "delta": delta,
                "rel": rel,
                "wild_magnitude": wild,
            }
        )
    return rows


def main() -> int:
    capacity = _emit_capacity()
    sio = _emit_sio()
    staged = _emit_staged()

    flat = {
        "capacity_total_kg_hr": capacity["total_kg_hr"],
        "capacity_non_phosphorus_kg_hr": capacity["non_phosphorus_kg_hr"],
        "capacity_transport_saturation_pct": capacity["transport_saturation_pct"],
        "capacity_melt_mass_kg": capacity["melt_mass_kg"],
        "sio_evolved_kg": sio["sio_evolved_kg"],
        "sio_stage3_silica_kg": sio["sio_stage3_silica_kg"],
        "sio_wall_deposit_1050_kg": sio["sio_wall_deposit_1050_kg"],
        "staged_silica_kg": staged["staged_silica_kg"],
        "staged_product_sio_kg": staged["staged_product_sio_kg"],
    }
    honesty = _honesty_rows(flat)
    findings = [r for r in honesty if r["wild_magnitude"]]

    payload = {
        "tip_note": "studio grind engines.local.toml pin emission",
        "capacity_head_result": capacity,
        "sio": sio,
        "staged_bakeout": staged,
        "pins": flat,
        "honesty_gate": honesty,
        "findings": findings,
        "finding_count": len(findings),
    }
    REPORT_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"report": str(REPORT_PATH.relative_to(ROOT)), **{
        "finding_count": len(findings),
        "pins": flat,
    }}, indent=2, sort_keys=True))
    # Always exit 0 after writing the report so studio-regen pullback runs;
    # the local worker treats finding_count > 0 as a FINDING (not a regen).
    if findings:
        print("HONESTY_FINDINGS:", len(findings), file=sys.stderr)
        for f in findings:
            print(
                f"  {f['key']}: laptop={f['laptop']!r} studio={f['studio']!r} "
                f"rel={f['rel']!r}",
                file=sys.stderr,
            )
    else:
        print("HONESTY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
