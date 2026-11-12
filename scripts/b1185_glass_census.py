#!/usr/bin/env python3
"""Print per-scenario Stage 3 silica glass qualification census as JSONL."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import yaml

from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun
from simulator.session import SimSession
from simulator.three_product_report import (
    _stage_3_glass_snapshot_census,
    classify_products,
)


RUNNER_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "runner"
OPTIMIZER_PROFILE_DIR = REPO_ROOT / "data" / "optimize_profiles"


def _census(sim: Any) -> dict[str, Any]:
    snapshots = getattr(getattr(sim, "record", None), "snapshots", ()) or ()
    census = _stage_3_glass_snapshot_census(snapshots)
    if census["issue"] is not None:
        raise ValueError(census["issue"])
    return {
        "qualified_glass_kg": census["product_kg"],
        "excluded_kg_by_reason": census["excluded_kg_by_reason"],
        "si_coproduct_kg": census["silicon_coproduct_kg"],
        "qualified_si_coproduct_kg": census["qualified_silicon_coproduct_kg"],
        "snapshot_count": len(snapshots),
    }


def _run_case(
    *,
    case_id: str,
    case_kind: str,
    feedstock_id: str,
    campaign: str,
    hours: int,
    mass_kg: float,
    additives_kg: Mapping[str, float] | None = None,
    setpoints_patch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runner = PyrolysisRun(
        feedstock_id=feedstock_id,
        campaign=campaign,
        hours=hours,
        mass_kg=mass_kg,
        additives_kg=dict(additives_kg or {}),
        setpoints_patch=dict(setpoints_patch or {}),
        backend_name=ANALYTICAL_BACKEND_SERIALIZATION_TOKEN,
    )
    session = SimSession().start(runner._session_config())
    execution = RunExecutor().execute_session(session, hours=hours)
    # A refused or short run has no Stage-3 evidence. Publishing glass 0
    # would turn that refusal into a measured yield (m_type_metallic_phase
    # C0: liquid_fraction_provider_failed, 0 snapshots).
    if execution.status != "ok":
        raise ValueError(
            f"run_status={execution.status}: "
            f"{execution.reason or execution.error_message or 'no reason'}"
        )
    sim = execution.simulator
    snapshots = getattr(getattr(sim, "record", None), "snapshots", ()) or ()
    if len(snapshots) != hours:
        raise ValueError(
            f"run_snapshots={len(snapshots)} requested_hours={hours}"
        )
    classification = classify_products(sim)
    bucket = classification["pure_silica_glass"]
    flag = bucket.get("flag", {})
    classified_glass_kg = bucket.get("class_total_kg")
    if classified_glass_kg is None:
        raise ValueError(str(flag.get("reason") or "glass classification unavailable"))
    census = _census(sim)
    # Same number published twice. An absolute tolerance would accept a trace
    # disagreement as zero; refuse any difference.
    if census["qualified_glass_kg"] != float(classified_glass_kg):
        raise ValueError(
            "snapshot census glass mass does not match product classification"
        )
    result = {
        "case_id": case_id,
        "case_kind": case_kind,
        "feedstock_id": feedstock_id,
        "campaign": campaign,
        "hours": hours,
        **census,
        "classified_glass_kg": classified_glass_kg,
        "flag_present": "owner product spec pending" in str(
            flag.get("reason", "")
        ),
        "flag_reason": flag.get("reason"),
    }
    return result


def _runner_scenarios() -> list[dict[str, Any]]:
    cases = []
    for path in sorted(RUNNER_FIXTURE_DIR.glob("*.json")):
        document = json.loads(path.read_text())
        metadata = document.get("run_metadata", {})
        cases.append({
            "case_id": path.stem,
            "case_kind": "shipped_runner_scenario",
            "feedstock_id": metadata["feedstock_id"],
            "campaign": metadata["campaign"],
            "hours": int(metadata["hours_requested"]),
            "mass_kg": float(metadata["mass_kg"]),
            "additives_kg": metadata.get("additives_kg", {}),
        })
    return cases


def _c0_profiles() -> list[dict[str, Any]]:
    """Every configured C0 profile, at its configured duration.

    A duration cap would leave the 60-hour profiles unmeasured. The census
    the purity-spec question needs is the full configured set.
    """
    cases = []
    for path in sorted(OPTIMIZER_PROFILE_DIR.rglob("*.yaml")):
        profile = yaml.safe_load(path.read_text()) or {}
        case_id = path.relative_to(OPTIMIZER_PROFILE_DIR).with_suffix("").as_posix()
        run = profile.get("run", {})
        if run.get("campaign") != "C0":
            continue
        hours = int(run["hours"])
        seed_recipes = profile.get("seed_recipes", ()) or ()
        patch: Mapping[str, Any] = {}
        if seed_recipes:
            patch = RecipeSchema().to_setpoints_patch(
                RecipePatch.from_nested(seed_recipes[0]["patch"])
            )
        cases.append({
            "case_id": case_id,
            "case_kind": "optimizer_c0_profile",
            "feedstock_id": profile["feedstock"],
            "campaign": "C0",
            "hours": hours,
            "mass_kg": float(run.get("mass_kg", 1000.0)),
            "additives_kg": dict(run.get("additives_kg") or {}),
            "setpoints_patch": patch,
        })
    return cases


def main() -> int:
    had_errors = False
    for case in [*_runner_scenarios(), *_c0_profiles()]:
        try:
            result = _run_case(**case)
        except Exception as exc:
            had_errors = True
            result = {
                "case_id": case["case_id"],
                "case_kind": case["case_kind"],
                "status": "unavailable",
                "reason": f"{type(exc).__name__}: {exc}",
                "qualified_glass_kg": None,
                "excluded_kg_by_reason": None,
                "si_coproduct_kg": None,
                "qualified_si_coproduct_kg": None,
                "flag_present": None,
            }
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    return int(had_errors)


if __name__ == "__main__":
    raise SystemExit(main())
