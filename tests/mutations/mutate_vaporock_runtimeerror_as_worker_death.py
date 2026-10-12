"""Prove a raw RuntimeError from our pool code is not worker death.

Widening the lifecycle catch to RuntimeError launders a local defect into
not_converged.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/melt_backend/vaporock.py"
TEST = (
    "tests/test_vaporock_backend.py::"
    "test_warm_pool_local_runtimeerror_propagates"
)
NARROW = (
    "        except EngineWorkerDied as exc:\n"
    "            named = (\n"
    "                exc.__cause__\n"
    "                if isinstance(exc.__cause__, BaseException)\n"
    "                else exc\n"
    "            )\n"
    "            raise VapoRockCallError(named) from exc\n"
)
MUTANT = NARROW.replace(
    "except EngineWorkerDied as exc:",
    "except (EngineWorkerDied, RuntimeError) as exc:",
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(NARROW) != 1:
        raise SystemExit(
            "expected one worker-death classification, "
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
            "mutation survived: local RuntimeError stayed loud"
        )
    print("mutation killed: raw RuntimeError was classified as worker death")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
