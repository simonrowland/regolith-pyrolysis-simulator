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
import shlex
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
    import yaml
    from simulator.state import CampaignPhase
    from tests.chemistry.conftest import _build_sim

    data = ROOT / "data"

    def load(name: str) -> dict:
        return yaml.safe_load((data / name).read_text())

    sim = _build_sim(
        "lunar_mare_low_ti",
        load("vapor_pressures.yaml"),
        load("feedstocks.yaml"),
        load("setpoints.yaml"),
    )
    sim.start_campaign(CampaignPhase.C0)
    sim.melt.temperature_C = 1600.0
    # Match test_default_off_preserves_hot_fe_redox_split_head_result: default-off
    # live path must not compute capacity shadow.
    sim._compute_capacity_coupling_shadow = lambda _eq: (_ for _ in ()).throw(
        AssertionError("default-off live path must not compute capacity")
    )
    snapshot = sim.step()
    return {
        "hour": snapshot.hour,
        "temperature_C": snapshot.temperature_C,
        "total_kg_hr": float(snapshot.evap_flux.total_kg_hr),
        "transport_saturation_pct": float(snapshot.overhead.transport_saturation_pct),
        "melt_mass_kg": float(snapshot.melt_mass_kg),
        "n_transitions": len(sim.atom_ledger.transitions),
        "last5_reasons": [
            t.reason for t in sim.atom_ledger.transitions[-5:]
        ],
        "mass_balance_error_pct": float(snapshot.mass_balance_error_pct),
    }


def _emit_sio() -> dict:
    from simulator.runner import build_sio_yield_report

    def report_at(liner_c: float):
        return build_sio_yield_report(
            feedstock_id="lunar_mare_low_ti",
            hours=24,
            mass_kg=1000.0,
            include_diagnostics=True,
            liner_temperature_c=liner_c,
            pO2_mbar=None,
            allow_unmeasured_alpha_fallback=True,
        )

    evolved = []
    for liner in (1050.0, 1300.0, 1400.0, 1500.0):
        report, diagnostics = report_at(liner)
        evolved.append(float(report["sio_evolved_kg"]))
        assert abs(float(diagnostics["mass_balance_error_pct"])) <= 5e-12

    report_1400, _ = report_at(1400.0)
    report_1050, _ = report_at(1050.0)
    wall_1050 = report_1050["wall_deposit_kg"]
    wall_1400 = report_1400["wall_deposit_kg"]
    wall_1500 = report_at(1500.0)[0]["wall_deposit_kg"]

    stage3 = float(
        report_1400["sio_to_silica_fume_kg"]["stage_3_sio_zone_product"]
    )
    deposit_1050 = float(wall_1050.get("Si", 0.0)) + float(
        wall_1050.get("SiO2", 0.0)
    )
    deposit_1400 = float(wall_1400.get("Si", 0.0)) + float(
        wall_1400.get("SiO2", 0.0)
    )
    deposit_1500 = float(wall_1500.get("Si", 0.0)) + float(
        wall_1500.get("SiO2", 0.0)
    )

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
        "sio_evolved_wall_T_spread_kg": spread,
        "sio_stage3_silica_kg": stage3,
        "sio_wall_deposit_1050_kg": deposit_1050,
        "sio_wall_deposit_1400_kg": deposit_1400,
        "sio_wall_deposit_1500_kg": deposit_1500,
        "wall_SiO_kg_1050": float(wall_1050.get("SiO", 0.0)),
    }


def _emit_staged() -> dict:
    from simulator.session_cli import SessionScriptRunner
    import simulator.session_cli as session_cli_module

    FEEDSTOCK = "lunar_mare_low_ti"
    NA_DOSE_KG = 12.0
    HOT_HOLD_C = 1750.0

    def run_staged():
        runner = SessionScriptRunner()
        lines = [
            (
                f"start --feedstock={FEEDSTOCK} --campaign=C2A_staged "
                f"--additive=Na={NA_DOSE_KG}"
            ),
            f"adjust campaign_override C2A_staged hold_temp_C {HOT_HOLD_C}",
            "advance 30",
        ]
        for line in lines:
            if line.startswith("start "):
                original = session_cli_module.load_config_bundle

                def load_with_alpha(*args, **kwargs):
                    bundle = original(*args, **kwargs)
                    setpoints = dict(bundle.setpoints)
                    kernel = dict(setpoints.get("chemistry_kernel", {}) or {})
                    kernel["allow_unmeasured_alpha_fallback"] = True
                    setpoints["chemistry_kernel"] = kernel
                    return replace(bundle, setpoints=setpoints)

                with patch.object(
                    session_cli_module, "load_config_bundle", load_with_alpha
                ):
                    runner.execute(shlex.split(line), line)
            else:
                runner.execute(shlex.split(line), line)
        return runner.session._sim

    sim = run_staged()
    products = sim.product_ledger()
    sio_stage = sim.train.stages[3].collected_kg
    staged_silica = float(sio_stage.get("Si", 0.0)) + float(
        sio_stage.get("SiO2", 0.0)
    )
    return {
        "staged_silica_kg": staged_silica,
        "staged_product_sio_kg": float(products.get("SiO", 0.0)),
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
