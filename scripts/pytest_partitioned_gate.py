#!/usr/bin/env python3
"""Run the deterministic two-pass PR-tier pytest gate.

Bulk tests inherit pyproject's ``-n auto`` xdist addopts. Tests marked
``serial`` are run in a second ``-n0`` pass so coscheduling-sensitive families
stay covered without making the whole suite serial. Nightly tests remain in the
separate full-tier gate.
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


def _known_red_junit_path(extra: list[str], repo_root: Path) -> Path:
    existing: Path | None = None
    for index, argument in enumerate(extra):
        if argument.startswith("--junitxml="):
            existing = Path(argument.partition("=")[2])
        elif argument == "--junitxml" and index + 1 < len(extra):
            existing = Path(extra[index + 1])
    if existing is None:
        return repo_root / "known-red.xml"
    if not existing.is_absolute():
        existing = repo_root / existing
    suffix = existing.suffix or ".xml"
    stem = existing.stem if existing.suffix else existing.name
    return existing.with_name(f"{stem}-known-red{suffix}")


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
        (
            "bulk",
            [python, "-m", "pytest", "-m", "not serial and not nightly", *extra],
        ),
        (
            "serial",
            [python, "-m", "pytest", "-n0", "-m", "serial and not nightly", *extra],
        ),
    ]

    gate_exit_code = 0
    for name, cmd in buckets:
        code = _run_bucket(name, cmd, repo_root)
        if code != 0:
            gate_exit_code = code
            break

    if "--collect-only" in extra:
        print("Known-red result skipped (--collect-only)")
        return gate_exit_code

    known_red_junit = _known_red_junit_path(extra, repo_root)
    known_red_junit.unlink(missing_ok=True)
    known_red_code = _run_bucket(
        "known-red",
        [
            python,
            "-m",
            "pytest",
            "tests/",
            "--known-red-only",
            f"--junitxml={known_red_junit}",
        ],
        repo_root,
    )
    print(
        f"Known-red result (non-gating): exit={known_red_code} "
        f"junit={known_red_junit}"
    )
    return gate_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
