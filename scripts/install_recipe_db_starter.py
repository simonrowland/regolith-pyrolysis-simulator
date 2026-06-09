#!/usr/bin/env python3
"""Unpack the shipped starter recipe DB into the optimizer runs root.

Idempotent + NON-DESTRUCTIVE: only unpacks when the runs root has no recipe-DB
studies yet, so a user's own runs are never overwritten. Safe to call from the
installer on every run. Target = $OPTIMIZER_RUNS_DIR else <repo>/runs.
"""
from __future__ import annotations

import os
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARCHIVE = REPO / "data" / "recipe-db-starter.tgz"


def runs_root() -> Path:
    env = os.environ.get("OPTIMIZER_RUNS_DIR")
    return Path(env).expanduser() if env else (REPO / "runs")


def main() -> int:
    if not ARCHIVE.exists():
        print(f"recipe-db starter: no archive at {ARCHIVE}; skipping")
        return 0
    dest = runs_root()
    if dest.exists() and any(dest.glob("*/cache.sqlite")):
        print(f"recipe-db starter: {dest} already has studies; leaving it alone")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVE, "r:gz") as tar:
        # Hardening: refuse any member that would escape dest (path traversal).
        safe = []
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                print(f"recipe-db starter: refusing unsafe member {m.name}")
                return 1
            safe.append(m)
        # 'data' filter (py3.12+) blocks path traversal / device/links; our
        # explicit check above is belt-and-suspenders for older interpreters.
        try:
            tar.extractall(dest, members=safe, filter="data")
        except TypeError:
            tar.extractall(dest, members=safe)
    n = len(list(dest.glob("*/cache.sqlite")))
    print(f"recipe-db starter: unpacked {n} studies into {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
