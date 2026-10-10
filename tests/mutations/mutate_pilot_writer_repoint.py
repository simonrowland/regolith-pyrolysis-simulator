"""Restore each newly retired route and require its regression to fail."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "tools" / "migrate_pilot_extracts.py"
TEST = ROOT / "tests" / "test_migrate_pilot_retired_sources.py"
PYTHON = Path("/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python")
MUTATIONS = (
    '        "pound_1972_cr_langmuir_knudsen",\n',
    '        "safarian_engh_2013_si_pure_langmuir",\n',
)


def main() -> int:
    original = WRITER.read_text(encoding="utf-8")
    environment = os.environ | {"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    for route in MUTATIONS:
        if original.count(route) != 1:
            raise SystemExit(f"expected exactly one retirement entry: {route.strip()}")
        WRITER.write_text(original.replace(route, "", 1), encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    str(PYTHON),
                    "-m",
                    "pytest",
                    "-n",
                    "0",
                    "-p",
                    "no:cacheprovider",
                    "--timeout=300",
                    "--tb=short",
                    "-q",
                    str(TEST),
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                timeout=300,
                capture_output=True,
                text=True,
            )
        finally:
            WRITER.write_text(original, encoding="utf-8")
        output = result.stdout + result.stderr
        if result.returncode == 0:
            raise SystemExit(f"mutation survived: {route.strip()}")
        lowered = output.lower()
        if any(
            marker in lowered
            for marker in ("error during collection", "error collecting", "collection errors")
        ):
            raise SystemExit(f"mutation was not proven; pytest collection failed:\n{output}")
        if "FAILED tests/test_migrate_pilot_retired_sources.py" not in output:
            raise SystemExit(f"mutation did not fail the regression assertion:\n{output}")
        print(f"RED as required: restoring {route.strip()} fails the destination assertion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
