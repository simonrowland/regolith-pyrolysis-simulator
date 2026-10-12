"""Prove a native-body TypeError is not retried as a signature mismatch.

Treating every TypeError as argument rejection lets the next candidate
replace a failed solve with a number.
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
    "test_native_body_typeerror_does_not_fall_through"
)
NARROW = '''            elif positional_binds:
                try:
                    raw = fn(*legacy_args)
                except Exception as exc:
                    raise VapoRockCallError(exc) from exc
'''
MUTANT = '''            elif positional_binds:
                try:
                    raw = fn(*legacy_args)
                except TypeError as exc:
                    traceback = exc.__traceback__
                    if traceback is not None and traceback.tb_next is None:
                        last_attr_error = exc
                        continue
                    raise VapoRockCallError(exc) from exc
                except Exception as exc:
                    raise VapoRockCallError(exc) from exc
'''


def main() -> int:
    original = SOURCE.read_text()
    if original.count(NARROW) != 1:
        raise SystemExit(
            "expected one accepted positional-call handler, "
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
            "mutation survived: native TypeError did not fall through"
        )
    print("mutation killed: native-body TypeError fell through as a mismatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
