"""Prove a warm child EOF before ready remains a typed initialization failure."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/melt_backend/vaporock.py"
TEST = (
    "tests/test_vaporock_backend.py::"
    "test_warm_worker_exit_before_ready_is_typed"
)
NARROW = (
    "        except EngineWorkerDied as exc:\n"
    "            named = (\n"
    "                exc.__cause__\n"
    "                if isinstance(exc.__cause__, BaseException)\n"
    "                else exc\n"
    "            )\n"
    "            self._last_error = (\n"
)
MUTANT = NARROW.replace(
    "except EngineWorkerDied as exc:",
    "except TypeError as exc:",
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(NARROW) != 1:
        raise SystemExit(
            "expected one pre-ready EOF boundary, "
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
        raise SystemExit("mutation survived: pre-ready worker EOF regression passed")
    print("mutation killed: EOFError escapes warm startup without the narrow catch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
