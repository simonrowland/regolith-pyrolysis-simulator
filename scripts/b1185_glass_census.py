#!/usr/bin/env python3
"""Print per-scenario Stage 3 silica glass qualification census as JSONL."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

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


REPO_ROOT = Path(__file__).resolve().parents[1]
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
    sim = execution.simulator
    classification = classify_products(sim)
    bucket = classification["pure_silica_glass"]
    flag = bucket.get("flag", {})
    classified_glass_kg = bucket.get("class_total_kg")
    if classified_glass_kg is None:
        raise ValueError(str(flag.get("reason") or "glass classification unavailable"))
    census = _census(sim)
    if not math.isclose(
        census["qualified_glass_kg"],
        float(classified_glass_kg),
        rel_tol=1.0e-9,
        abs_tol=1.0e-9,
    ):
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


def _c0_profiles(max_hours: int) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(OPTIMIZER_PROFILE_DIR.rglob("*.yaml")):
        profile = yaml.safe_load(path.read_text()) or {}
        case_id = path.relative_to(OPTIMIZER_PROFILE_DIR).with_suffix("").as_posix()
        run = profile.get("run", {})
        if run.get("campaign") != "C0":
            continue
        hours = int(run["hours"])
        if hours > max_hours:
            print(json.dumps({
                "case_id": case_id,
                "case_kind": "optimizer_c0_profile",
                "status": "skipped",
                "reason": "profile_run_hours_exceeds_limit",
                "configured_hours": hours,
                "max_profile_hours": max_hours,
                "qualified_glass_kg": None,
                "excluded_kg_by_reason": None,
                "si_coproduct_kg": None,
                "flag_present": None,
            }, sort_keys=True))
            continue
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
            "setpoints_patch": patch,
        })
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-profile-hours",
        type=int,
        default=25,
        help="skip C0 profiles whose configured duration exceeds this bound",
    )
    args = parser.parse_args()
    if args.max_profile_hours < 0:
        parser.error("--max-profile-hours must be non-negative")

    had_errors = False
    for case in [*_runner_scenarios(), *_c0_profiles(args.max_profile_hours)]:
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
                "flag_present": None,
            }
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    return int(had_errors)


if __name__ == "__main__":
    raise SystemExit(main())
