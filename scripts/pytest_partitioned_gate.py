#!/usr/bin/env python3
"""Run the deterministic two-pass pytest gate.

Bulk tests inherit pyproject's ``-n auto`` xdist addopts. Tests marked
``serial`` are run in a second ``-n0`` pass so coscheduling-sensitive families
stay covered without making the whole suite serial.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _run_bucket(name: str, args: list[str], repo_root: Path) -> int:
    env = os.environ.copy()
    env["REGOLITH_PYTEST_BUCKET"] = name
    print(f"== pytest {name} bucket ==")
    print(" ".join(args))
    completed = subprocess.run(args, cwd=repo_root, env=env)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run pytest as xdist bulk + serial -n0 buckets."
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="extra pytest args forwarded to both buckets; prefix with -- when needed",
    )
    parsed = parser.parse_args(argv)
    extra = list(parsed.pytest_args)
    if extra[:1] == ["--"]:
        extra = extra[1:]

    repo_root = Path(__file__).resolve().parents[1]
    python = sys.executable
    buckets = [
        ("bulk", [python, "-m", "pytest", "-m", "not serial", *extra]),
        ("serial", [python, "-m", "pytest", "-n0", "-m", "serial", *extra]),
    ]

    for name, cmd in buckets:
        code = _run_bucket(name, cmd, repo_root)
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
