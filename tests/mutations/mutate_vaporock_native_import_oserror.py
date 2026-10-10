"""Prove the native-library import OSError remains an unavailable outcome."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/melt_backend/vaporock.py"
TEST = (
    "tests/test_vaporock_backend.py::"
    "test_provider_selection_falls_back_with_native_init_failure_reason"
)
NARROW = (
    "            try:\n"
    "                return importlib.import_module(module_name)\n"
    "            except (ImportError, OSError) as exc:\n"
    "                errors.append(f'{module_name}: {type(exc).__name__}: {exc}')\n"
)
MUTANT = NARROW.replace(
    "except (ImportError, OSError) as exc:",
    "except ImportError as exc:",
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(NARROW) != 1:
        raise SystemExit(
            "expected one native import boundary, "
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
            "mutation survived: native import OSError regression passed"
        )
    print(
        "mutation killed: OSError escapes provider selection without "
        "the narrow catch"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
