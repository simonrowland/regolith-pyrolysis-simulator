"""Prove the Sossi rows fail when valence quantity classification is removed."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/battery/identity.py"
TARGET = r'''_PRINTED_VALENCE_QUANTITY = re.compile(
    r"(?P<log10>log10_)?(?P<element>[A-Z][a-z]?)(?P<numerator>\d+)"
    r"(?P<numerator_sign>plus|minus)_over_"
    r"(?:(?P<total_kind>total|sum)_(?P<total_element>[A-Z][a-z]?)|"
    r"(?P<denominator_element>[A-Z][a-z]?)(?P<denominator>\d+)"
    r"(?P<denominator_sign>plus|minus))"
)'''
MUTANT = '_PRINTED_VALENCE_QUANTITY = re.compile(r"(?!)")'
TEST = (
    "tests/battery/test_valence_ratio.py::"
    "test_sossi_2018_quoted_cr_rows_migrate_as_numeric_valence_fractions"
)


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8")
    if original.count(TARGET) != 1:
        raise RuntimeError("could not identify the printed valence parser regex")
    try:
        SOURCE.write_text(original.replace(TARGET, MUTANT, 1), encoding="utf-8")
        env = dict(os.environ)
        env.setdefault(
            "MPLCONFIGDIR",
            str(Path(tempfile.gettempdir()) / "matplotlib"),
        )
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-vv",
                "-n",
                "0",
                "--timeout=300",
                TEST,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        SOURCE.write_text(original, encoding="utf-8")
    output = result.stdout + result.stderr
    if result.returncode != 1 or "FAILED" not in output:
        print("unsupported-valence mutation was not rejected")
        print("\n".join(output.splitlines()[-18:]))
        return 1
    print(f"mutation rejected by {TEST}")
    print("\n".join(output.splitlines()[-18:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
