#!/usr/bin/env python3
"""Generate per-studio C6 cache-warming grind manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
GRIND_DIR = REPO_ROOT / "docs-private" / "grind"
MOON_EXTRA = frozenset({"targeted_super_kreep_ore"})
S_TYPE_FEEDSTOCK = "s_type_asteroid_silicate"
C6_TARGETS = frozenset(
    {
        "pc-extract-na",
        "pc-extract-k",
        "pc-extract-fe",
        "pc-extract-mg",
        "pc-extract-al",
        "pc-extract-o2",
        "pc-glass-clear",
        "pc-ceramic-ca-al-ree",
        "pc-ceramic-ca-al-ratio-seed",
    }
)
EXCLUDED_TARGETS = frozenset({"pc-glass-retain-na-k-c3"})

SCOPE_DESCRIPTION = (
    "C6 per-studio cache-warming grind (trajectory re-traversal, not feasibility search). "
    "Includes pc-extract-* as cache-warming cells (expected no_feasible; shards still merge per "
    "epoch_grind.py). Includes earnable pc-glass-clear + pc-ceramic-* targets. "
    "Excludes pc-glass-retain-na-k-c3 (owner-adjudicated infeasible)."
)

DEFAULT_SEED_BASE = 202606130000
DEFAULT_N_SEEDS = 3
DEFAULT_BUDGET = 192
DEFAULT_PARALLEL = 8


def is_moon_feedstock(feedstock: str) -> bool:
    return feedstock.startswith("lunar_") or feedstock in MOON_EXTRA


def is_mars_stype_feedstock(feedstock: str) -> bool:
    return not is_moon_feedstock(feedstock)


def load_feedstock_keys(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data.keys())


def partition_feedstocks(
    feedstocks: list[str],
    *,
    feedstock_predicate,
    feedstock_subset: frozenset[str] | None,
) -> list[str]:
    selected: list[str] = []
    for feedstock in feedstocks:
        if not feedstock_predicate(feedstock):
            continue
        if feedstock_subset is not None and feedstock not in feedstock_subset:
            continue
        selected.append(feedstock)
    return selected


def feedstocks_with_optimize_profiles(feedstocks: list[str]) -> list[str]:
    opt_dir = REPO_ROOT / "data" / "optimize_profiles"
    available = {path.stem for path in opt_dir.glob("*.yaml")}
    return [feedstock for feedstock in feedstocks if feedstock in available]


def build_cells(feedstocks: list[str]) -> list[dict[str, str]]:
    cells: list[dict[str, str]] = []
    for feedstock in feedstocks:
        for target in sorted(C6_TARGETS):
            if target in EXCLUDED_TARGETS:
                continue
            cells.append(
                {
                    "feedstock": feedstock,
                    "target": target,
                    "profile": f"profiles/{feedstock}__{target}.real.yaml",
                }
            )
    return cells


def make_job(
    cell: dict[str, str],
    *,
    run_root: str,
    cell_index: int,
    seed_index: int,
    budget: int,
    parallel: int,
    seed_base: int,
) -> dict[str, object]:
    feedstock = cell["feedstock"]
    target = cell["target"]
    base_id = f"{feedstock}__{target}"
    job_id = f"{base_id}__s{seed_index}"
    seed = seed_base + cell_index * 10 + seed_index
    return {
        "id": job_id,
        "feedstock": feedstock,
        "profile": cell["profile"],
        "budget": budget,
        "strategy": "random",
        "seed": seed,
        "out": f"{run_root}/runs/{job_id}",
        "fidelity": "high",
        "parallel": parallel,
    }


def build_manifest(
    *,
    studio_label: str,
    run_root: str,
    cells: list[dict[str, str]],
    skipped_feedstocks: list[str],
    n_seeds: int,
    seed_base: int,
    budget: int,
    parallel: int,
) -> dict[str, object]:
    jobs: list[dict[str, object]] = []
    for cell_index, cell in enumerate(cells):
        for seed_index in range(n_seeds):
            jobs.append(
                make_job(
                    cell,
                    run_root=run_root,
                    cell_index=cell_index,
                    seed_index=seed_index,
                    budget=budget,
                    parallel=parallel,
                    seed_base=seed_base,
                )
            )
    skipped_note = ""
    if skipped_feedstocks:
        skipped_note = (
            " Skipped feedstocks without shipped data/optimize_profiles YAML "
            f"(no trajectory): {', '.join(skipped_feedstocks)}."
        )
    return {
        "description": f"{SCOPE_DESCRIPTION} Studio partition: {studio_label}.{skipped_note}",
        "base_cache": f"{run_root}/cache/base.sqlite",
        "work_dir": f"{run_root}/work",
        "fidelity": "high",
        "parallel": parallel,
        "jobs": jobs,
    }


def filter_manifest_by_feedstock(
    manifest: dict[str, object],
    *,
    feedstock: str,
) -> dict[str, object]:
    filtered = dict(manifest)
    filtered["jobs"] = [
        job
        for job in manifest["jobs"]
        if isinstance(job, dict) and job.get("feedstock") == feedstock
    ]
    return filtered


def write_manifest(path: Path, payload: dict[str, object]) -> None:
    path.write_text(f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--n-seeds",
        type=int,
        default=DEFAULT_N_SEEDS,
        help="distinct optimizer seeds per (feedstock,target) cell",
    )
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE)
    parser.add_argument(
        "--moon-run-root",
        default="~/grind-c6-moon",
        help="run root path embedded in moon-studio manifest job out dirs",
    )
    parser.add_argument(
        "--mars-run-root",
        default="~/grind-c6-mars-stype",
        help="run root path embedded in mars+stype manifest job out dirs",
    )
    parser.add_argument(
        "--feedstock-subset",
        nargs="*",
        default=None,
        help="optional feedstock keys for a smaller first wave (applies to both studios)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=GRIND_DIR,
        help="directory for manifest JSON outputs",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.n_seeds < 1:
        print("error: --n-seeds must be >= 1", file=sys.stderr)
        return 2

    feedstocks = load_feedstock_keys(REPO_ROOT / "data" / "feedstocks.yaml")
    subset = frozenset(args.feedstock_subset) if args.feedstock_subset else None
    if subset is not None:
        unknown = sorted(subset - frozenset(feedstocks))
        if unknown:
            print(f"error: unknown feedstock subset keys: {unknown}", file=sys.stderr)
            return 2

    moon_feedstocks = partition_feedstocks(
        feedstocks,
        feedstock_predicate=is_moon_feedstock,
        feedstock_subset=subset,
    )
    mars_feedstocks = partition_feedstocks(
        feedstocks,
        feedstock_predicate=is_mars_stype_feedstock,
        feedstock_subset=subset,
    )
    moon_ready = feedstocks_with_optimize_profiles(moon_feedstocks)
    mars_ready = feedstocks_with_optimize_profiles(mars_feedstocks)
    moon_skipped = sorted(set(moon_feedstocks) - set(moon_ready))
    mars_skipped = sorted(set(mars_feedstocks) - set(mars_ready))
    moon_cells = build_cells(moon_ready)
    mars_cells = build_cells(mars_ready)

    moon_manifest = build_manifest(
        studio_label="moon (lunar_* + targeted_super_kreep_ore)",
        run_root=args.moon_run_root,
        cells=moon_cells,
        skipped_feedstocks=moon_skipped,
        n_seeds=args.n_seeds,
        seed_base=args.seed_base,
        budget=args.budget,
        parallel=args.parallel,
    )
    mars_manifest = build_manifest(
        studio_label="mars+s-type (mars_* + asteroid/chondrite/comet)",
        run_root=args.mars_run_root,
        cells=mars_cells,
        skipped_feedstocks=mars_skipped,
        n_seeds=args.n_seeds,
        seed_base=args.seed_base,
        budget=args.budget,
        parallel=args.parallel,
    )
    stype_manifest = filter_manifest_by_feedstock(
        mars_manifest,
        feedstock=S_TYPE_FEEDSTOCK,
    )
    if not stype_manifest["jobs"]:
        print(f"error: mars manifest has no jobs for {S_TYPE_FEEDSTOCK}", file=sys.stderr)
        return 2

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    moon_path = out_dir / "manifest-c6-moon-studio1.json"
    mars_path = out_dir / "manifest-c6-mars-stype-studio2.json"
    stype_path = out_dir / "manifest-c6-stype-studio2.json"
    write_manifest(moon_path, moon_manifest)
    write_manifest(mars_path, mars_manifest)
    write_manifest(stype_path, stype_manifest)

    moon_feedstocks = {cell["feedstock"] for cell in moon_cells}
    mars_feedstocks = {cell["feedstock"] for cell in mars_cells}
    overlap = sorted(moon_feedstocks & mars_feedstocks)
    if overlap:
        print(f"error: feedstock partition overlap: {overlap}", file=sys.stderr)
        return 2

    print(
        f"wrote {moon_path.name}: cells={len(moon_cells)} jobs={len(moon_manifest['jobs'])} "
        f"feedstocks={len(moon_ready)} skipped_no_optimize_profile={moon_skipped}"
    )
    print(
        f"wrote {mars_path.name}: cells={len(mars_cells)} jobs={len(mars_manifest['jobs'])} "
        f"feedstocks={len(mars_ready)} skipped_no_optimize_profile={mars_skipped}"
    )
    print(
        f"wrote {stype_path.name}: jobs={len(stype_manifest['jobs'])} "
        f"feedstock={S_TYPE_FEEDSTOCK}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
