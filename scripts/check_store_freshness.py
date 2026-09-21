#!/usr/bin/env python3
"""Tripwire: is the committed derived store stale w.r.t. migrate inputs?

The derived store (works/, extracts-v2/, observations-v2/, migration-report.md)
is what consumers read, but nothing asserts it was regenerated after the
extracts, compilations, or migration code last changed. This script is the
cheap half of that assertion: it finds the newest commit that touched store
outputs and fails if any later commit touched migrate inputs.

It cannot prove byte equality — that needs a full regen + diff (minutes,
nightly-tier). It proves the cheaper fact: nobody changed an input and walked
away without touching the store.

Exit 0: store is at least as new as every migrate input.
Exit 1: input commits landed after the last store touch (store suspect).

Usage: scripts/check_store_freshness.py [--head REF]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STORE_OUTPUTS = (
    "data/literature/works",
    "data/literature/extracts-v2",
    "data/literature/observations-v2",
    "data/battery/migration-report.md",
)

# Everything battery_migrate.py reads that is not itself a store output:
# extract sources (extracts/*.yaml minus _*/SCHEMA*), compilation sources
# (compilations/**/*.{yaml,yml,json}), the index, named-source ledgers that
# live as top-level data/literature/*.yaml, the lab-parameter vocabulary,
# and the migration code.
INPUT_EXACT = {
    "data/literature/INDEX.yaml",
    "data/literature/lab_parameter_vocabulary.yaml",
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def _is_input(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if path in INPUT_EXACT or path.startswith("simulator/battery/"):
        return True
    if path.startswith("data/literature/extracts/"):
        return (
            path.endswith(".yaml")
            and path.count("/") == 3
            and not name.startswith("_")
            and not name.upper().startswith("SCHEMA")
        )
    if path.startswith("data/literature/compilations/"):
        return (
            path.endswith((".yaml", ".yml", ".json"))
            and name != "README.md"
            and "__pycache__" not in path
        )
    # Named-source ledgers: top-level data/literature/*.yaml only.
    return (
        path.startswith("data/literature/")
        and path.endswith(".yaml")
        and path.count("/") == 2
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--head", default="HEAD", help="ref to inspect (default: HEAD)")
    args = parser.parse_args(argv)
    head = _git("rev-parse", args.head).strip()

    last_store = _git("log", "-1", "--format=%H", head, "--", *STORE_OUTPUTS).strip()
    if not last_store:
        print("no commit touches the derived store; nothing to check against")
        return 1

    stale: dict[str, list[str]] = {}
    log = _git(
        "log", "--format=%H%x00%s", "--name-only", f"{last_store}..{head}", "--",
        "data/literature", "simulator/battery",
    )
    commit: str | None = None
    subject = ""
    for line in log.splitlines():
        if "\x00" in line:
            commit, subject = line.split("\x00", 1)
        elif line.strip() and commit is not None and _is_input(line.strip()):
            stale.setdefault(f"{commit[:9]} {subject}", []).append(line.strip())

    short = _git("log", "-1", "--format=%h %ad %s", "--date=short", last_store).strip()
    if not stale:
        print(f"OK: store last touched at {short}; no later migrate-input commits")
        return 0
    print(f"STALE: store last touched at {short}")
    print(f"{len(stale)} later commit(s) touched migrate inputs without a store regen:")
    for title, paths in stale.items():
        sample = ", ".join(paths[:3]) + (" ..." if len(paths) > 3 else "")
        print(f"  {title}\n    {sample}")
    print("regenerate: .venv/bin/python scripts/battery_migrate.py && "
          "data/literature/build_index.py --write-store-summary")
    return 1


if __name__ == "__main__":
    sys.exit(main())
