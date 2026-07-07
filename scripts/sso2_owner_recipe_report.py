#!/usr/bin/env python3
"""Generate the SSO-2 owner recipe evidence report."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.optimize.sso2_evidence import (  # noqa: E402
    SSO2_CHUNK3B_READER_HANDOFF,
    SSO2_OWNER_RECIPE_ID,
    build_sso2_owner_recipe_execution,
    sso2_owner_recipe_evidence,
)


DEFAULT_OUTPUT = (
    REPO_ROOT
    / "docs-private"
    / "research"
    / "2026-07-02-sso2-scope"
    / "sso2-owner-recipe-report.md"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--hours", type=int, default=9)
    parser.add_argument("--backend-name", default="stub")
    parser.add_argument("--json", action="store_true", help="also write a .json sidecar")
    args = parser.parse_args()

    execution = build_sso2_owner_recipe_execution(
        hours=args.hours,
        backend_name=args.backend_name,
    )
    evidence = sso2_owner_recipe_evidence(execution)
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_markdown_report(evidence, execution), encoding="utf-8")
    if args.json:
        output.with_suffix(".json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"status: {evidence['status']}")
    print(f"report: {_display_path(output)}")
    return 0


def _markdown_report(evidence: Mapping[str, Any], execution: Any) -> str:
    stage3 = evidence["stage_3"]
    tap = evidence["fe_tap"]
    metal = evidence["metal_product_path"]
    impurity = evidence["stage_1_or_metal_tap_si_impurity"]
    partition = evidence["fe_drain_vapor_partition_dependency"]
    surface = evidence["certified_sso_r_surface"]
    purity = evidence["delivered_stream_purity"]
    coating = evidence["wall_coating"]
    mass_balance = evidence["mass_balance"]
    lines = [
        "# SSO-2 Owner Recipe Evidence Report",
        "",
        f"- recipe: `{SSO2_OWNER_RECIPE_ID}`",
        f"- source_anchor: `{_git_sha()}`",
        f"- run_status: `{getattr(execution, 'status', 'unknown')}`",
        f"- evidence_status: `{evidence['status']}`",
        f"- evidence_status_reason: {_fmt(evidence.get('status_reason'))}",
        f"- chunk_3b_reader_handoff: {SSO2_CHUNK3B_READER_HANDOFF}",
        "",
        "## Recipe Surface",
        "",
        "- feedstock: `lunar_mare_low_ti`",
        "- high_temperature_legs_C: `1650-1670`",
        "- stage_order: `fe_then_sio`",
        (
            "- SSO_R_observed_stage_gas_surface: "
            f"`{surface['stage_gas_snapshot'].get('gas_cover_mode')}`, "
            f"`stage={surface['stage_gas_snapshot'].get('stage_name')}`, "
            f"`pO2_mbar={_fmt(surface['pO2_mbar'])}`, "
            f"`pN2_mbar={_fmt(surface['pN2_mbar'])}`, "
            f"`p_total_mbar={_fmt(surface['p_total_mbar'])}`"
        ),
        (
            "- SSO_R_declared_gas_surface: `pn2_sweep`, "
            f"`pO2_mbar={surface['declared_pO2_mbar']}`, "
            f"`pN2_mbar={surface['declared_pN2_mbar']}`, "
            f"`p_total_mbar={surface['declared_p_total_mbar']}`"
        ),
        (
            "- SSO_R_certified_Na_dose: "
            f"`{surface['dose_species']}` {_fmt(surface['dose_kg'])} kg; "
            f"transition_count `{surface['dose_transition_count']}`"
        ),
        "- pN2_heuristic_band_mbar: `5-15`; this preset uses `10`",
        "- Fe_threshold: none invented in chunk 3a",
        "",
        "## Stage 3 Silica Evidence",
        "",
        f"- status: `{stage3['status']}`",
        f"- status_reason: {_fmt(stage3.get('status_reason'))}",
        f"- accepted_species: `{', '.join(stage3['accepted_species'])}`",
        f"- accepted_species_reader: `{stage3['accepted_species_reader']}`",
        f"- SiO_kg: {_fmt(stage3['silica_species_kg']['SiO'])}",
        f"- SiO_mol: {_fmt(stage3['silica_species_mol']['SiO'])}",
        f"- SiO2_kg: {_fmt(stage3['silica_species_kg']['SiO2'])}",
        f"- SiO2_mol: {_fmt(stage3['silica_species_mol']['SiO2'])}",
        f"- Si_kg: {_fmt(stage3['silica_species_kg']['Si'])}",
        f"- Si_mol: {_fmt(stage3['silica_species_mol']['Si'])}",
        f"- Fe_kg: {_fmt(stage3['Fe_kg'])}",
        f"- Fe_wt_pct: {_fmt(stage3['Fe_wt_pct'])}",
        f"- total_kg: {_fmt(stage3['total_kg'])}",
        f"- stage_3_purity_fraction: {_fmt(stage3['purity_fraction'])}",
        f"- stage_3_purity_margin_vs_profile_threshold: {_fmt(stage3['purity_margin'])}",
        "",
        "## Delivered Stream Purity Gate",
        "",
        f"- feasible: `{purity['feasible']}`",
        f"- observed: {_fmt(purity['observed'])}",
        f"- margin: {_fmt(purity['margin'])}",
        f"- detail: {purity['detail']}",
        f"- threshold: `{purity['threshold']['id']}={purity['threshold']['value']}`",
        "",
        "## Fe Tap And Product Evidence",
        "",
        f"- tap_status: `{tap['status']}`",
        f"- tap_status_reason: {_fmt(tap.get('status_reason'))}",
        f"- tap_account: `{tap['account']}`",
        f"- tap_Fe_kg: {_fmt(tap['Fe_kg'])}",
        f"- tap_total_kg: {_fmt(tap['total_kg'])}",
        f"- tap_SiO_Si_impurity_kg: {_fmt(tap['SiO_Si_impurity_kg'])}",
        f"- tap_SiO_Si_impurity_wt_pct: {_fmt(tap['SiO_Si_impurity_wt_pct'])}",
        f"- metal_phase_status: `{metal['status']}`",
        f"- metal_phase_Fe_kg: {_fmt(metal['Fe_kg'])}",
        f"- product_ledger_Fe_kg: {_fmt(metal['product_ledger_Fe_kg'])}",
        f"- stage_1_SiO_Si_impurity_kg: {_fmt(impurity['stage_1_SiO_Si_impurity_kg'])}",
        f"- stage_1_SiO_Si_impurity_wt_pct: {_fmt(impurity['stage_1_SiO_Si_impurity_wt_pct'])}",
        "",
        "## Dependency Status",
        "",
        f"- fe_drain_vapor_partition_status: `{partition['status']}`",
        f"- fe_drain_vapor_partition_reason: {_fmt(partition['status_reason'])}",
        f"- native_fe_saturation_split_count: `{partition['native_fe_saturation_split_count']}`",
        "",
        "## Wall And Mass Balance",
        "",
        f"- coating_feasible: `{coating['feasible']}`",
        f"- coating_margin: {_fmt(coating['margin'])}",
        f"- coating_detail: {coating['detail']}",
        f"- mass_balance_status: `{mass_balance['status']}`",
        f"- mass_balance_max_abs_error_pct: {_fmt(mass_balance['max_abs_error_pct'])}",
        "",
        "## Golden Status",
        "",
        "Tracked goldens not regenerated. This report is docs-private only.",
        "",
    ]
    return "\n".join(lines)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _fmt(value: Any) -> str:
    if value is None:
        return "`pending`"
    if isinstance(value, float):
        return f"`{value:.12g}`"
    return f"`{value}`"


if __name__ == "__main__":
    raise SystemExit(main())
