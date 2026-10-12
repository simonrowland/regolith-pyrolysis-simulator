"""Prove a restart before ready stays a typed lifecycle failure.

Restoring the raw EOF reraise makes the pre-ready death escape equilibrate.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/engine_pool.py"
TEST = (
    "tests/test_vaporock_backend.py::"
    "test_warm_worker_restart_exit_before_ready_is_typed"
)
NARROW = (
    "            raise EngineWorkerDied(\n"
    "                'start',\n"
    "                f'{type(exc).__name__}: {exc}',\n"
    "            ) from exc\n"
)
MUTANT = "            raise\n"


def main() -> int:
    original = SOURCE.read_text()
    if original.count(NARROW) != 1:
        raise SystemExit(
            "expected one pre-ready death normalisation, "
            f"found {original.count(NARROW)}"
        )

    SOURCE.write_text(original.replace(NARROW, MUTANT, 1))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-n",
                "0",
                "--timeout=300",
                "-p",
                "no:cacheprovider",
                TEST,
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
    finally:
        SOURCE.write_text(original)

    if result.returncode == 0:
        raise SystemExit(
            "mutation survived: restart-before-ready death stayed typed"
        )
    print("mutation killed: pre-ready restart EOF escapes without the pool type")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
