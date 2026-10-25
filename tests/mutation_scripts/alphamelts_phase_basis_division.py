"""Prove the alphaMELTS per-formula-mole test catches lost normalization."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "simulator/battery/compilation_tier.py"
TARGET = """            normalized[destination] = (
                extensive / formula_moles
                if math.isfinite(extensive)
                else None
            )"""
MUTATION = """            normalized[destination] = (
                extensive
                if math.isfinite(extensive)
                else None
            )"""
TEST = (
    "tests/battery/test_alphamelts_compilation_thermo.py::"
    "test_alpha_phase_extensive_values_normalize_to_formula_moles"
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError("could not identify alphaMELTS formula-mole normalization")
    try:
        SOURCE.write_text(original.replace(TARGET, MUTATION, 1))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
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
            timeout=300,
            check=False,
        )
    finally:
        SOURCE.write_text(original)
    output = result.stdout + result.stderr
    if result.returncode != 1 or "FAILED" not in output:
        print("alphaMELTS basis-division mutation was not rejected")
        print("\n".join(output.splitlines()[-18:]))
        return 1
    print(f"basis-division mutation rejected by {TEST}")
    print("\n".join(output.splitlines()[-18:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
