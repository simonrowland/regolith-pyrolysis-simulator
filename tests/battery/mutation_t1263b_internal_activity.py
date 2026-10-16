#!/usr/bin/env python3
"""Prove the Guo, Cho, and extrapolation tests fail without activity wiring.

Run after committing the implementation, with the seat's venv Python and
MPLCONFIGDIR exported. The pinned pre-wiring scorer blob supplies the defect;
this runner restores the committed scorer even when a mutation check errors.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


TESTS = (
    "tests/battery/test_internal_analytical_battery_engine.py::"
    "test_internal_analytical_scores_guo_2021_mgo_row",
    "tests/battery/test_internal_analytical_battery_engine.py::"
    "test_internal_analytical_scores_cho_suito_1994_sio2_row",
    "tests/battery/test_internal_analytical_battery_engine.py::"
    "test_internal_analytical_predicts_below_gamma_band_with_notice",
)


def git_file(root: Path, revision: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{revision}:simulator/battery/score.py"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def git_blob(root: Path, blob: str) -> bytes:
    result = subprocess.run(
        ["git", "cat-file", "blob", blob],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    if not os.environ.get("MPLCONFIGDIR"):
        raise SystemExit("export MPLCONFIGDIR before running mutation tests")

    root = Path(__file__).resolve().parents[2]
    score_path = root / "simulator" / "battery" / "score.py"
    committed = git_file(root, "HEAD")
    reverted = git_blob(root, "996f896afed1daad125202d067eb39fd61e63879")
    if score_path.read_bytes() != committed:
        raise SystemExit("score.py differs from HEAD; refusing to overwrite work")

    environment = os.environ.copy()
    old_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(root)
        if not old_pythonpath
        else str(root) + os.pathsep + old_pythonpath
    )
    report = [
        "Mutation: simulator/battery/score.py restored from pinned pre-wiring blob "
        "996f896afed1daad125202d067eb39fd61e63879 for each test.",
        "Expected result: each focused test is RED because melt_oxide_activity is unwired.",
        "",
    ]
    all_red = True
    try:
        score_path.write_bytes(reverted)
        for test in TESTS:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q", "-n", "0", test],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                output = result.stdout + result.stderr
                red = result.returncode != 0 and f"FAILED {test}" in output
                report.extend(
                    (
                        f"{'RED' if red else 'NOT RED'}: {test}",
                        f"exit_code: {result.returncode}",
                        output.rstrip(),
                        "",
                    )
                )
            except subprocess.TimeoutExpired as exc:
                red = False
                report.extend(
                    (
                        f"NOT RED: {test}",
                        "timed out after 300 seconds",
                        str(exc),
                        "",
                    )
                )
            all_red = all_red and red
    finally:
        score_path.write_bytes(committed)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(line for line in report if line.startswith(("RED:", "NOT RED:"))))
    print(f"report: {args.report}")
    return 0 if all_red else 1


if __name__ == "__main__":
    raise SystemExit(main())
