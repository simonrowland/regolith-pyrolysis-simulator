#!/usr/bin/env python3
"""Generate a REAL-fidelity optimizer profile from a shipped stub profile.

Flips backend stub->cached-real with live-fill, wiring the alphamelts subprocess
+ a shared reduced-real equilibrium cache. The `authorized_backend_version` is
queried from the LOCAL runtime engine (it embeds the binary path, so it is
machine-specific) — generate on the machine that will run the study.

Usage:
  make_recipe_db_profile.py <feedstock_id> [--campaign C2A_continuous]
      [--hours 30] [--gate stub_smoke|physics] [--db <cache.db>] [--out <path>]
      [--target <menu-id>|all]
Writes the real-fidelity profile to --out (default docs-private/recipe-db/profiles/<id>.real.yaml).
"""
import argparse
import copy
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DESIGN_TARGET_PROVENANCE = (
    "design-composition-target-objective-2026-06-10 rev 3.2 PC target menu seed"
)
STANDARD_COST_METRICS = ("energy_kWh", "duration_h")
MENU_TARGET_IDS = (
    "pc-extract-na",
    "pc-extract-k",
    "pc-extract-fe",
    "pc-extract-mg",
    "pc-pure-silica-captured",
    "pc-extract-al",
    "pc-extract-o2",
    "pc-glass-clear",
    "pc-glass-green",
    "pc-glass-retain-na-k-c3",
    "pc-ceramic-ca-al-ree",
    "pc-ceramic-ca-al-ratio-seed",
    "pc-ceramic-ca-ree-after-al",
)


@dataclass(frozen=True)
class TargetMenuRow:
    target_id: str
    pool: str
    species_vector: Mapping[str, str]
    oxides: Mapping[str, Mapping[str, Any]]
    maturity_campaign: str
    maturity_hours: int
    ratios: tuple[Mapping[str, Any], ...] = ()
    extraction_min: Mapping[str, float] | None = None
    score_weights: Mapping[str, float] | None = None


def _extract_row(
    target_id: str,
    species: str,
    *,
    campaign: str,
    hours: int,
    completeness_min: float,
) -> TargetMenuRow:
    species_ids = ("Na", "K", "Fe", "Mg", "Si", "Al", "Ca", "O2")
    return TargetMenuRow(
        target_id=target_id,
        pool="captured_products",
        species_vector={
            key: "extract" if key == species else "free"
            for key in species_ids
        },
        oxides={},
        maturity_campaign=campaign,
        maturity_hours=hours,
        extraction_min={species: completeness_min},
        score_weights={"extraction": 1.0},
    )


TARGET_MENU: Mapping[str, TargetMenuRow] = {
    "pc-extract-na": _extract_row(
        "pc-extract-na",
        "Na",
        campaign="C2A_continuous",
        hours=24,
        completeness_min=0.95,
    ),
    "pc-extract-k": _extract_row(
        "pc-extract-k",
        "K",
        campaign="C2A_continuous",
        hours=24,
        completeness_min=0.90,
    ),
    "pc-extract-fe": _extract_row(
        "pc-extract-fe",
        "Fe",
        campaign="C2B",
        hours=24,
        completeness_min=0.85,
    ),
    "pc-extract-mg": _extract_row(
        "pc-extract-mg",
        "Mg",
        campaign="C4",
        hours=24,
        completeness_min=1.0,
    ),
    "pc-extract-al": _extract_row(
        "pc-extract-al",
        "Al",
        campaign="C6",
        hours=24,
        completeness_min=1.0,
    ),
    "pc-extract-o2": _extract_row(
        "pc-extract-o2",
        "O2",
        campaign="C2A_continuous",
        hours=24,
        completeness_min=1.0,
    ),
    "pc-glass-clear": TargetMenuRow(
        target_id="pc-glass-clear",
        pool="residual_rump_at_stop",
        species_vector={
            "Na": "extract",
            "K": "extract",
            "Fe": "extract",
            "Mg": "retain",
            "Ca": "retain",
            "Al": "retain",
            "Si": "retain",
            "O2": "free",
        },
        extraction_min={"Na": 0.95, "K": 0.90, "Fe": 0.85},
        oxides={
            "FeO_total": {
                "min": 0.0,
                "max": 0.5,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
                "provenance": "owner_seed_clear_fe_style",
            },
            "Al2O3": {
                "min": 15.0,
                "max": 20.0,
                "strict": False,
                "weight": 2.0,
                "needs_experiment": True,
                "provenance": "owner_seed_loose_stabilizer_style",
            },
        },
        maturity_campaign="C2B",
        maturity_hours=24,
        score_weights={"extraction": 0.50, "composition": 0.50},
    ),
    "pc-glass-retain-na-k-c3": TargetMenuRow(
        target_id="pc-glass-retain-na-k-c3",
        pool="residual_rump_at_stop",
        species_vector={
            "Na": "retain",
            "K": "retain",
            "Fe": "free",
            "Mg": "retain",
            "Ca": "retain",
            "Al": "retain",
            "Si": "retain",
            "O2": "free",
        },
        oxides={
            "Na2O_plus_K2O": {
                "min": 5,
                "max": 18,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Fe_total_as_Fe2O3_wt_pct": {
                "tier": "workable_glass",
                "needs_experiment": True,
            },
            "SiO2": {"min": 45, "max": 75, "weight": 1.0, "needs_experiment": True},
            "Al2O3_CaO_MgO_balance": {
                "min": 15,
                "max": 45,
                "weight": 1.0,
                "needs_experiment": True,
            },
        },
        maturity_campaign="C3",
        maturity_hours=24,
        score_weights={"extraction": 0.0, "composition": 1.0},
    ),
    "pc-ceramic-ca-al-ree": TargetMenuRow(
        target_id="pc-ceramic-ca-al-ree",
        pool="terminal_rump_earned",
        species_vector={
            "Na": "extract",
            "K": "extract",
            "Fe": "extract",
            "Mg": "extract",
            "Si": "extract",
            "Ca": "retain",
            "Al": "retain",
            "O2": "free",
        },
        extraction_min={"Na": 0.95, "K": 0.90, "Fe": 0.85, "Mg": 0.85, "Si": 0.85},
        oxides={
            "CaO": {"min": 20, "max": 60, "weight": 1.0, "needs_experiment": True},
            "Al2O3": {"min": 10, "max": 45, "weight": 1.0, "needs_experiment": True},
            "TiO2_plus_Cr2O3_plus_REO": {
                "min": 1,
                "max": 25,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Na2O_plus_K2O": {
                "min": 0,
                "max": 2,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Fe_total_as_Fe2O3_wt_pct": {
                "min": 0,
                "max": 5,
                "weight": 1.0,
                "needs_experiment": True,
            },
        },
        maturity_campaign="C4",
        maturity_hours=24,
        score_weights={"extraction": 0.50, "composition": 0.50},
    ),
    "pc-ceramic-ca-al-ratio-seed": TargetMenuRow(
        target_id="pc-ceramic-ca-al-ratio-seed",
        pool="terminal_rump_earned",
        species_vector={
            "Na": "extract",
            "K": "extract",
            "Fe": "extract",
            "Mg": "extract",
            "Si": "extract",
            "Ca": "retain",
            "Al": "retain",
            "O2": "free",
        },
        extraction_min={"Na": 0.95, "K": 0.90, "Fe": 0.85, "Mg": 0.85, "Si": 0.85},
        oxides={
            "CaO": {"min": 20, "max": 60, "strict": True, "weight": 1.0, "needs_experiment": True},
            "Al2O3": {"min": 10, "max": 45, "strict": True, "weight": 1.0, "needs_experiment": True},
            "TiO2_plus_Cr2O3_plus_REO": {
                "min": 1,
                "max": 25,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Na2O_plus_K2O": {
                "min": 0,
                "max": 2,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
            },
            "Fe_total_as_Fe2O3_wt_pct": {
                "min": 0,
                "max": 5,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
            },
        },
        ratios=(
            {
                "numerator": "CaO",
                "denominator": "Al2O3",
                "min": 0.45,
                "max": 0.75,
                "strict": True,
                "weight": 1.0,
                "needs_experiment": True,
                "provenance": "owner_seed_calcium_aluminate_composition",
            },
        ),
        maturity_campaign="C4",
        maturity_hours=24,
        score_weights={"extraction": 0.50, "composition": 0.50},
    ),
}


def _runtime_engine_identity() -> tuple[str, str]:
    from simulator.melt_backend.alphamelts import AlphaMELTSBackend
    b = AlphaMELTSBackend()
    b.initialize({"mode": None})
    name = getattr(b, "name", None) or "alphamelts"
    getter = getattr(b, "get_engine_version", None)
    version = str(getter()).strip() if callable(getter) else ""
    if not version:
        raise SystemExit("could not resolve runtime engine version")
    return str(name), version


def _load_base_profile(feedstock: str) -> dict[str, Any]:
    src = REPO_ROOT / "data" / "optimize_profiles" / f"{feedstock}.yaml"
    if not src.exists():
        raise SystemExit(f"no shipped profile: {src}")
    profile = yaml.safe_load(src.read_text())
    if not isinstance(profile, dict):
        raise SystemExit(f"invalid shipped profile: {src}")
    return profile


def _cached_real_config(db_path: str, name: str, version: str) -> dict[str, str]:
    return {
        "db_path": db_path,
        "miss_policy": "live-fill",
        "authorized_backend_name": name,
        "authorized_backend_version": version,
    }


def _apply_cached_real(
    profile: dict[str, Any],
    *,
    campaign: str,
    hours: int,
    gate: str,
    cache: Mapping[str, str],
) -> None:
    profile["study_constraints"] = gate
    run = dict(profile.get("run") or {})
    run.update({
        "campaign": campaign,
        "hours": hours,
        "backend_name": "cached-real",
        "reduced_real_cache": dict(cache),
    })
    profile["run"] = run
    fid = dict(profile.get("fidelities") or {})
    fid["high"] = {
        "backend_name": "cached-real",
        "hours": hours,
        "reduced_real_cache": dict(cache),
    }
    profile["fidelities"] = fid


def _with_row_provenance(
    oxides: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    copied: dict[str, dict[str, Any]] = {}
    for oxide, row in oxides.items():
        copied[str(oxide)] = dict(row)
    return copied


def _target_objective(row: TargetMenuRow, *, campaign: str, hours: int) -> dict[str, Any]:
    extraction_min = dict(row.extraction_min or {})
    extraction = {
        "basis": "input_element_mol",
        "captured_pool": (
            "captured_stage_3_silica"
            if row.pool == "captured_stage_3_silica"
            else "captured_products"
        ),
        "credit_policy": {
            "additives": "no_product_credit",
            "vented": "no_product_credit",
        },
        "completeness_min": extraction_min,
    }
    target = {
        "pool": row.pool,
        "require_coating_gate": True,
        "species_vector": dict(row.species_vector),
        "extraction": extraction,
        "maturity": {
            "mode": "campaign_hours",
            "campaign": campaign,
            "hours": hours,
        },
        "constraints": {
            "coating_min_campaigns_to_resinter": "profile_default",
            "furnace_T_max_C": "profile_or_study_constraint",
        },
        "score_weights": dict(row.score_weights or {"extraction": 0.5, "composition": 0.5}),
    }
    if row.oxides or row.ratios:
        window: dict[str, Any] = {
            "pool": row.pool,
            "basis": "oxide_wt_pct",
            "mode": "hard_window",
            "exploratory": False,
            "oxides": _with_row_provenance(row.oxides),
        }
        if row.ratios:
            window["ratios"] = [dict(ratio) for ratio in row.ratios]
        target["composition_window"] = window
    return {
        "type": "composition_target",
        "id": row.target_id,
        "metric": f"composition_target:{row.target_id}",
        "sense": "maximize",
        "units": "score_0_1",
        "weight": 1.0,
        "rationale": "PC target matrix seed; bounds are provisional.",
        "target": target,
    }


def _standard_cost_objectives(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    objectives = profile.get("objectives") or []
    if not isinstance(objectives, list):
        raise SystemExit("base profile objectives must be a list")
    by_metric = {
        objective.get("metric"): dict(objective)
        for objective in objectives
        if isinstance(objective, Mapping)
    }
    missing = [metric for metric in STANDARD_COST_METRICS if metric not in by_metric]
    if missing:
        raise SystemExit(f"base profile missing standard cost objectives: {', '.join(missing)}")
    return [by_metric[metric] for metric in STANDARD_COST_METRICS]


def _target_profile(
    base_profile: Mapping[str, Any],
    row: TargetMenuRow,
    *,
    campaign: str,
    hours: int,
) -> dict[str, Any]:
    profile = copy.deepcopy(dict(base_profile))
    feedstock = str(profile["feedstock"])
    profile["profile_id"] = f"{feedstock}-{row.target_id}-recipe-db-profile-v1"
    profile["description"] = f"{feedstock} PC target matrix profile for {row.target_id}."
    profile["north_star_rationale"] = (
        f"Score {row.target_id} from the rev 2.1 PC target menu while retaining "
        "standard energy and duration minimization."
    )
    profile["objective_emphasis"] = f"PC target matrix: {row.target_id}."
    profile["objectives"] = [
        _target_objective(row, campaign=campaign, hours=hours),
        *_standard_cost_objectives(profile),
    ]
    return profile


def _plain_data(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_data(item) for item in value]
    if isinstance(value, list):
        return [_plain_data(item) for item in value]
    return value


def _validated_profile(profile: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    from simulator.optimize.profiles import ProfileValidationError, validate_profile

    try:
        validated = validate_profile(
            copy.deepcopy(dict(profile)),
            expected_feedstock=str(profile["feedstock"]),
            source=source,
        )
    except ProfileValidationError as exc:
        raise SystemExit(f"generated profile failed validation: {exc}") from exc
    return _plain_data(validated)


def _resolve_target_rows(raw_targets: list[str] | None) -> list[TargetMenuRow]:
    if not raw_targets:
        return []
    selected: list[str] = []
    for raw in raw_targets:
        if raw == "all":
            selected.extend(TARGET_MENU)
        else:
            selected.append(raw)
    rows: list[TargetMenuRow] = []
    seen: set[str] = set()
    for target_id in selected:
        if target_id in seen:
            continue
        seen.add(target_id)
        if target_id not in MENU_TARGET_IDS:
            known = ", ".join(MENU_TARGET_IDS)
            raise SystemExit(f"unknown PC target {target_id!r}; known targets: {known}")
        try:
            rows.append(TARGET_MENU[target_id])
        except KeyError as exc:
            raise SystemExit(
                f"PC target {target_id!r} has no rev 3.2 seed window; refusing to invent bounds"
            ) from exc
    return rows


def _output_path(feedstock: str, target_id: str | None, out_arg: str | None, count: int) -> Path:
    if target_id is None:
        return Path(out_arg) if out_arg else (
            REPO_ROOT / "docs-private" / "recipe-db" / "profiles" / f"{feedstock}.real.yaml"
        )
    default = (
        REPO_ROOT
        / "docs-private"
        / "recipe-db"
        / "profiles"
        / f"{feedstock}__{target_id}.real.yaml"
    )
    if out_arg is None:
        return default
    out = Path(out_arg)
    if count == 1 and out.suffix in {".yaml", ".yml"}:
        return out
    if out.suffix in {".yaml", ".yml"}:
        raise SystemExit("--out must be a directory when emitting multiple target profiles")
    return out / f"{feedstock}__{target_id}.real.yaml"


def _write_profile(profile: Mapping[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(_plain_data(profile), sort_keys=False))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("feedstock")
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--hours", type=int, default=None)
    ap.add_argument("--gate", default="stub_smoke", choices=["stub_smoke", "physics"])
    ap.add_argument("--db", default="docs-private/recipe-db/reduced-real.db")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--target",
        action="append",
        default=None,
        help="PC target menu id to emit; repeatable, or 'all' for materialized seed rows",
    )
    args = ap.parse_args(argv)

    profile = _load_base_profile(args.feedstock)

    name, version = _runtime_engine_identity()
    cache = _cached_real_config(args.db, name, version)
    target_rows = _resolve_target_rows(args.target)

    if not target_rows:
        campaign = args.campaign or "C2A_continuous"
        hours = args.hours if args.hours is not None else 30
        _apply_cached_real(
            profile,
            campaign=campaign,
            hours=hours,
            gate=args.gate,
            cache=cache,
        )
        validated = _validated_profile(profile, source=f"<generated:{args.feedstock}>")
        out = _output_path(args.feedstock, None, args.out, 1)
        _write_profile(validated, out)
        print(f"wrote {out}")
        print(f"  engine: {name}@{version}")
        print(f"  campaign={campaign} hours={hours} gate={args.gate} db={args.db}")
        return 0

    for row in target_rows:
        campaign = args.campaign or row.maturity_campaign
        hours = args.hours if args.hours is not None else row.maturity_hours
        target_profile = _target_profile(profile, row, campaign=campaign, hours=hours)
        _apply_cached_real(
            target_profile,
            campaign=campaign,
            hours=hours,
            gate=args.gate,
            cache=cache,
        )
        validated = _validated_profile(
            target_profile,
            source=f"<generated:{args.feedstock}:{row.target_id}>",
        )
        out = _output_path(args.feedstock, row.target_id, args.out, len(target_rows))
        _write_profile(validated, out)
        print(f"wrote {out}")
        print(f"  target={row.target_id}")
        print(f"  engine: {name}@{version}")
        print(f"  campaign={campaign} hours={hours} gate={args.gate} db={args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
