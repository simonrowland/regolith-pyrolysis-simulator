"""Prove a missing Stolyarova point map fails its migration regression."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/literature/extracts/stolyarova-1996-cao-alumina-silica-kems.yaml"
TEST = (
    "tests/battery/test_migrate.py::"
    "test_stolyarova_1996_printed_compositions_reach_migrated_observations"
)


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8")
    mutated, count = re.subn(
        r"^\s*composition_mol: \{basis: normalized_mole_fraction_from_published_xA_over_xC_and_xS,.*\n",
        "",
        original,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise SystemExit("expected one located composition_mol point to remove")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["MPLCONFIGDIR"] = "/private/tmp/claude-501/main-review/mplcache"
    SOURCE.write_text(mutated, encoding="utf-8")
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
                TEST,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        SOURCE.write_text(original, encoding="utf-8")

    output = result.stdout + result.stderr
    if result.returncode != 1 or "assert 299 == 300" not in output:
        raise SystemExit(
            "one missing point map did not fail the composition regression\n"
            + output[-2000:]
        )
    print("PASS: removing one printed composition makes the migration regression fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
