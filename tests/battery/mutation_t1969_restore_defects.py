"""Restore one t-1969 regression at a time and require its full regression test to fail.

Run this only in a disposable Studio test tree, with the required native-library,
matplotlib, and MPI temp-directory environment set before starting Python.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MIGRATE = ROOT / "simulator/battery/migrate.py"
PROVENANCE = ROOT / "simulator/battery/input_provenance.py"
SCORE = ROOT / "simulator/battery/score.py"
HENAO_TEST = ROOT / "tests/test_henao_2001_ni_cr_mn_co_cu_slag_distribution_extract.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected one mutation anchor in {path}")
    path.write_text(text.replace(old, new, 1))


def restore(mutation: str) -> None:
    if mutation == "phase-label":
        replace_once(
            MIGRATE,
            "            if phase_role_token is not None:\n"
            "                phase = State.of(_PHASE_ROLE_THERMO_PHASE[phase_role_token])\n",
            "            if phase_raw is None or phase_raw == \"\":\n"
            "                assert phase_role_token is not None\n"
            "                phase = State.of(_PHASE_ROLE_THERMO_PHASE[phase_role_token])\n",
        )
    elif mutation == "alloy-capability":
        replace_once(
            SCORE,
            "    if role is PhaseRole.METAL and not preserve_input_diagnostic:\n",
            "    if False and role is PhaseRole.METAL and not preserve_input_diagnostic:\n",
        )
    elif mutation == "test-local":
        census = (
            "    henao_experiments = [\n"
            "        row\n"
            "        for row in result.experiments.values()\n"
            "        if row.experiment_id.startswith(\n"
            "            \"10.2320/matertrans.42.1959::experiment::henao:\"\n"
            "        )\n"
            "    ]\n"
        )
        replace_once(
            HENAO_TEST,
            census,
            "    first_sweep = henao_experiments[0].pressure_environment.sweep_gas\n"
            + census,
        )
    elif mutation == "locator-row":
        replace_once(
            PROVENANCE,
            '        "table",\n    )\n',
            '        "table",\n        "row",\n    )\n',
        )
    else:
        raise ValueError(mutation)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mutation",
        choices=("phase-label", "alloy-capability", "test-local", "locator-row"),
    )
    args = parser.parse_args()
    if not os.environ.get("TMPDIR"):
        parser.error("TMPDIR must point to the dispatch scratch directory")

    touched = {
        "phase-label": (MIGRATE,),
        "alloy-capability": (SCORE,),
        "test-local": (HENAO_TEST,),
        "locator-row": (PROVENANCE,),
    }[args.mutation]
    with tempfile.TemporaryDirectory(prefix="t1969-mutation-") as scratch:
        backups = {path: Path(scratch, path.name) for path in touched}
        for path, backup in backups.items():
            backup.write_bytes(path.read_bytes())
        try:
            restore(args.mutation)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-n",
                    "4",
                    "-p",
                    "no:cacheprovider",
                    str(HENAO_TEST),
                ],
                cwd=ROOT,
                check=False,
            )
            if result.returncode == 0:
                print(f"mutation survived: {args.mutation}", file=sys.stderr)
                return 1
            print(f"mutation detected: {args.mutation}")
            return 0
        finally:
            for path, backup in backups.items():
                path.write_bytes(backup.read_bytes())


if __name__ == "__main__":
    raise SystemExit(main())
