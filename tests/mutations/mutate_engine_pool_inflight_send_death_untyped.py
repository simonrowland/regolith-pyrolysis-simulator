"""Prove the in-poll state-loss path is EngineWorkerDied, not RuntimeError."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/engine_pool.py"
TEST = (
    "tests/test_engine_worker.py::"
    "test_inflight_disable_during_send_is_call_death"
)
NARROW = (
    "                    # In-flight state loss is worker death, not a local error.\n"
    "                    raise EngineWorkerDied(\n"
    "                        'call',\n"
    "                        f'{self.name} worker exited without a result',\n"
    "                    )\n"
)
MUTANT = (
    "                    raise RuntimeError(\n"
    "                        f'{self.name} worker exited without a result'\n"
    "                    )\n"
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(NARROW) != 1:
        raise SystemExit(
            "expected one in-poll call-death raise, "
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
            "mutation survived: in-poll state loss stayed typed"
        )
    print("mutation killed: plain RuntimeError escaped the send-disable path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
