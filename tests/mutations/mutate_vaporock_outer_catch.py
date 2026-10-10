"""Prove a broad equilibrate catch launders a local KeyError into status."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/melt_backend/vaporock.py"
TEST = (
    "tests/test_vaporock_backend.py::"
    "test_vaporock_equilibrate_does_not_convert_our_call_error"
)
NARROW = (
    "except VapoRockCallError as exc:\n"
    "            # VapoRock is present but the call did not produce a usable result."
)
BROAD = "except Exception as exc:"


def main() -> int:
    original = SOURCE.read_text()
    if original.count(NARROW) != 1:
        raise SystemExit(f"expected one narrow catch, found {original.count(NARROW)}")

    SOURCE.write_text(original.replace(NARROW, BROAD, 1))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-n",
                "0",
                "--timeout=300",
                TEST,
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
    finally:
        SOURCE.write_text(original)

    if result.returncode == 0:
        raise SystemExit("mutation survived: the focused regression test passed")
    print("mutation killed: broad catch makes the KeyError propagation test fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
