"""Restore the old broad SCSS catch and prove the local-error test fails."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/melt_backend/sulfsat.py"
TEST = "tests/test_sulfsat_gate.py::test_pysulfsat_failure_is_diagnostic_and_local_helper_errors_propagate"
OLD = "except _PySulfSatCallError as exc:"
MUTATED = "except Exception as exc:  # mutation restores SC-113 broad catch"


def main() -> int:
    original = SOURCE.read_text()
    if original.count(OLD) != 1:
        raise SystemExit(f"expected one narrow catch in {SOURCE}")

    SOURCE.write_text(original.replace(OLD, MUTATED, 1))
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-n",
                "0",
                TEST,
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        SOURCE.write_text(original)

    output = completed.stdout + completed.stderr
    if completed.returncode == 0:
        print(output)
        raise SystemExit("mutation survived: the focused regression test passed")
    if "DID NOT RAISE" not in output:
        print(output)
        raise SystemExit("mutation failed for a reason other than the regression")
    print("PASS: restored broad catch makes the focused regression test fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
