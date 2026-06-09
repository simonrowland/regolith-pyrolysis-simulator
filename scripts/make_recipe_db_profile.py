#!/usr/bin/env python3
"""Generate a REAL-fidelity optimizer profile from a shipped stub profile.

Flips backend stub->cached-real with live-fill, wiring the alphamelts subprocess
+ a shared reduced-real equilibrium cache. The `authorized_backend_version` is
queried from the LOCAL runtime engine (it embeds the binary path, so it is
machine-specific) — generate on the machine that will run the study.

Usage:
  make_recipe_db_profile.py <feedstock_id> [--campaign C2A_continuous]
      [--hours 30] [--gate stub_smoke|physics] [--db <cache.db>] [--out <path>]
Writes the real-fidelity profile to --out (default docs-private/recipe-db/profiles/<id>.real.yaml).
"""
import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("feedstock")
    ap.add_argument("--campaign", default="C2A_continuous")
    ap.add_argument("--hours", type=int, default=30)
    ap.add_argument("--gate", default="stub_smoke", choices=["stub_smoke", "physics"])
    ap.add_argument("--db", default="docs-private/recipe-db/reduced-real.db")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    src = REPO_ROOT / "data" / "optimize_profiles" / f"{args.feedstock}.yaml"
    if not src.exists():
        raise SystemExit(f"no shipped profile: {src}")
    profile = yaml.safe_load(src.read_text())

    name, version = _runtime_engine_identity()
    cache = {
        "db_path": args.db,
        "miss_policy": "live-fill",
        "authorized_backend_name": name,
        "authorized_backend_version": version,
    }
    # Flip run + high tier to cached-real; keep the shipped study gate unless overridden.
    profile["study_constraints"] = args.gate
    run = dict(profile.get("run") or {})
    run.update({"campaign": args.campaign, "hours": args.hours,
                "backend_name": "cached-real", "reduced_real_cache": dict(cache)})
    profile["run"] = run
    fid = dict(profile.get("fidelities") or {})
    fid["high"] = {"backend_name": "cached-real", "hours": args.hours,
                   "reduced_real_cache": dict(cache)}
    profile["fidelities"] = fid

    out = Path(args.out) if args.out else (
        REPO_ROOT / "docs-private" / "recipe-db" / "profiles" / f"{args.feedstock}.real.yaml")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(profile, sort_keys=False))
    print(f"wrote {out}")
    print(f"  engine: {name}@{version}")
    print(f"  campaign={args.campaign} hours={args.hours} gate={args.gate} db={args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
