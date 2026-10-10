"""Temporarily restore the retired alpha writer routes and require red tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "tools" / "migrate_pilot_extracts.py"
TEST = ROOT / "tests" / "test_migrate_pilot_retired_sources.py"
GUARD = '''        # d-084 retired these duplicate extracts; their curated kems-* owners
        # must never be rebuilt from the obsolete acquisition draft.
        if rid.startswith("fedkin") or "fedkin" in rid:
            continue
        if rid.startswith("sossi_2019") or "sossi_2019" in rid:
            continue
'''


def main() -> int:
    original = WRITER.read_text(encoding="utf-8")
    if original.count(GUARD) != 1:
        raise SystemExit("expected exactly one d-084 guard to mutate")
    WRITER.write_text(original.replace(GUARD, "", 1), encoding="utf-8")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-n",
                "0",
                "-p",
                "no:cacheprovider",
                "--tb=short",
                "-q",
                str(TEST),
            ],
            cwd=ROOT,
            check=False,
            timeout=300,
        )
    finally:
        WRITER.write_text(original, encoding="utf-8")
    if result.returncode == 0:
        raise SystemExit("mutation survived: owner-preservation regression stayed green")
    print("mutation detected: restored Fedkin/Sossi repoint makes the regression red")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
