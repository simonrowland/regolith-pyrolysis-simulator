#!/usr/bin/env python3
"""Parallel real-fidelity recipe-DB builder for one node's share.

For each (feedstock, campaign) pair: generate a cached-real profile (runtime
engine version), run an optimizer study at real fidelity, land a browsable
cache.sqlite under ~/recipe-db/runs/<feedstock>__<campaign>/. Each study gets
its OWN reduced-real equilibrium cache (no shared-SQLite write contention), so
studies run concurrently. nice'd for WarpX headroom.

Usage:
  recipe_db_build.py --feedstocks a,b,c --campaigns C2A_continuous,C2B,C4,C5 \
      [--concurrency 10] [--budget 16] [--gate physics] [--strategy screen] \
      [--timeout 900]
"""
import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME = Path(os.path.expanduser("~"))
PY = str(REPO / ".venv" / "bin" / "python")
BASE = HOME / "recipe-db"
RUNS = BASE / "runs"


def run_pair(f: str, c: str, budget: int, gate: str, strategy: str,
             timeout: int) -> dict:
    prof = BASE / "profiles" / f"{f}__{c}.real.yaml"
    out = RUNS / f"{f}__{c}"
    cache = BASE / "cache" / f"{f}__{c}.db"
    prof.parent.mkdir(parents=True, exist_ok=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    g = subprocess.run(
        [PY, str(REPO / "scripts" / "make_recipe_db_profile.py"), f,
         "--campaign", c, "--gate", gate, "--db", str(cache), "--out", str(prof)],
        capture_output=True, text=True, cwd=str(REPO))
    if g.returncode != 0:
        return {"pair": f"{f}:{c}", "status": "PROFGEN-FAIL",
                "detail": (g.stderr or g.stdout)[-300:]}
    if out.exists():
        shutil.rmtree(out)
    env = dict(os.environ, OPTIMIZER_RUNS_DIR=str(RUNS))
    try:
        r = subprocess.run(
            ["nice", "-n", "15", PY, "-m", "simulator.optimize",
             "--feedstock", f, "--profile", str(prof),
             "--strategy", strategy, "--fidelity", "high",
             "--budget", str(budget), "--parallel", "1",
             "--out", str(out), "--seed", "0"],
            capture_output=True, text=True, cwd=str(REPO), env=env,
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"pair": f"{f}:{c}", "status": "TIMEOUT"}
    status = "OK" if r.returncode == 0 else "FAIL"
    n_results = 0
    cache_db = cache if cache.exists() else None
    if (out / "cache.sqlite").exists():
        import sqlite3
        try:
            n_results = sqlite3.connect(out / "cache.sqlite").execute(
                "SELECT count(*) FROM results").fetchone()[0]
        except Exception:
            pass
    eq = 0
    if cache_db:
        import sqlite3
        try:
            eq = sqlite3.connect(cache_db).execute(
                "SELECT count(*) FROM reduced_real_equilibrium_payloads"
            ).fetchone()[0]
        except Exception:
            pass
    return {"pair": f"{f}:{c}", "status": status, "n_results": n_results,
            "equilibria": eq, "detail": (r.stderr or r.stdout)[-300:] if status == "FAIL" else ""}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feedstocks", required=True)
    ap.add_argument("--campaigns", required=True)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--budget", type=int, default=16)
    ap.add_argument("--gate", default="physics")
    ap.add_argument("--strategy", default="screen")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)
    feeds = [x for x in args.feedstocks.split(",") if x]
    camps = [x for x in args.campaigns.split(",") if x]
    pairs = [(f, c) for f in feeds for c in camps]
    print(f"recipe-db: {len(feeds)} feedstocks x {len(camps)} campaigns = "
          f"{len(pairs)} studies, conc={args.concurrency} budget={args.budget} "
          f"gate={args.gate}", flush=True)
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = {ex.submit(run_pair, f, c, args.budget, args.gate,
                          args.strategy, args.timeout): (f, c) for f, c in pairs}
        for fut in cf.as_completed(futs):
            res = fut.result()
            results.append(res)
            print(f"  [{len(results)}/{len(pairs)}] {res['status']:12} "
                  f"{res['pair']:42} results={res.get('n_results',0)} "
                  f"eq={res.get('equilibria',0)}", flush=True)
    ok = sum(1 for r in results if r["status"] == "OK")
    (BASE / "build-summary.json").write_text(json.dumps(results, indent=1))
    print(f"RECIPE-DB-BATCH-DONE ok={ok}/{len(pairs)} "
          f"summary={BASE/'build-summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
