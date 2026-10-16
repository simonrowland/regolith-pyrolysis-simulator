"""Prove the Sossi valence rows fail when printed valence parsing is removed."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/battery/migrate.py"
PYTHON = Path(
    "/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python"
)
TARGET = '''        if isinstance(raw, str) and "plus_over_" in raw:
            try:
                parse_valence_basis(raw)
            except IdentityRefusal as refusal:
                detail = f"{refusal.reason.value}: {refusal.detail}"
                return State.unknown(detail), detail
            return State.of(Quantity.VALENCE_RATIO), None
'''
TEST = (
    "tests/battery/test_valence_ratio.py::"
    "test_sossi_2018_quoted_cr_rows_migrate_as_numeric_valence_fractions"
)


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8")
    if original.count(TARGET) != 1:
        raise RuntimeError("could not identify printed valence quantity parsing")
    try:
        SOURCE.write_text(original.replace(TARGET, "", 1), encoding="utf-8")
        env = dict(os.environ)
        env["MPLCONFIGDIR"] = "/private/tmp/claude-501/main-review/mplcache"
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [
                str(PYTHON),
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
