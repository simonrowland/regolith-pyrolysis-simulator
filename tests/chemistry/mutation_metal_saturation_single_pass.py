"""Fail the slag test when the tie-point iteration is skipped.

The mutation keeps the pure-FeO IW fO2 (a_FeO = 1) after one activity
evaluation and does not update fO2. The slag test requires Fe3+/ΣFe below
that pure-IW edge, so this single pass must fail it.

Run from the seat root. The source file is restored before the process exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator" / "fe_redox.py"
ANCHOR = (
    "        if abs(next_fO2 - fO2_log) < _METAL_SATURATION_FO2_TOLERANCE_LOG10:\n"
    "            return next_fO2, components, iteration\n"
    "        fO2_log = next_fO2\n"
)
MUTATED = (
    "        # MUTATION: single pass at a_FeO = 1. Do not take the tie point.\n"
    "        return fO2_log, components, iteration\n"
)
TEST = (
    "tests/chemistry/test_metal_saturation_valence_split.py"
    "::test_tfe_slag_fe3_is_below_the_pure_iw_edge"
)


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8")
    if original.count(ANCHOR) != 1:
        print("mutation anchor missing or not unique", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("DYLD_FALLBACK_LIBRARY_PATH", "/Users/simonrowland/lib")
    env.setdefault(
        "MPLCONFIGDIR", "/private/tmp/claude-501/main-review/mplcache"
    )
    scratch = "/private/tmp/claude-501/main-review/w1105t1651b"
    env.setdefault("OMPI_MCA_orte_tmpdir_base", f"{scratch}/ompi")
    env.setdefault("PRTE_MCA_prte_tmpdir_base", f"{scratch}/ompi")
    env.setdefault("TMPDIR", f"{scratch}/tmp")
    python = (
        "/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python"
    )
    try:
        SOURCE.write_text(original.replace(ANCHOR, MUTATED, 1), encoding="utf-8")
        completed = subprocess.run(
            [
                python,
                "-m",
                "pytest",
                "-q",
                "-n",
                "0",
                "-p",
                "no:cacheprovider",
                "--timeout=300",
                TEST,
            ],
            cwd=ROOT,
            env=env,
            text=True,
        )
    finally:
        SOURCE.write_text(original, encoding="utf-8")
    print(f"mutation_exit={completed.returncode}")
    if completed.returncode == 0:
        print("mutation did not fail the test", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
