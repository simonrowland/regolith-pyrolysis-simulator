"""Restore one t-1969 regression and require its expected regression to fail.

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
ABDEYAZDAN_TEST = (
    ROOT / "tests/test_abdeyazdan_2026_na_sn_si_phase_thermodynamics_extract.py"
)

TEST_BY_MUTATION = {
    "phase-label": ABDEYAZDAN_TEST,
    "alloy-capability": HENAO_TEST,
    "test-local": HENAO_TEST,
    "locator-row": HENAO_TEST,
}
EXPECTED_FAILURE = {
    "phase-label": "explicit source phase must outrank phase role",
    "alloy-capability": (
        "all original metal-alloy rows must receive the adapter capability refusal"
    ),
    "test-local": "UnboundLocalError",
    "locator-row": "AttributeError: 'Locator' object has no attribute 'row'",
}
EXPECTED_NODE = {
    "phase-label": "test_isolated_migration_preserves_phase_conditions_and_printed_values",
    "alloy-capability": "test_henao_tables_migrate_as_typed_compositions_with_conditions",
    "test-local": "test_henao_tables_migrate_as_typed_compositions_with_conditions",
    "locator-row": "test_henao_tables_migrate_as_typed_compositions_with_conditions",
}


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected one mutation anchor in {path}")
    path.write_text(text.replace(old, new, 1))


def restore(mutation: str) -> None:
    if mutation == "phase-label":
        replace_once(
            MIGRATE,
            "            if phase_role_token is not None and phase.is_unknown:\n"
            "                phase = State.of(_PHASE_ROLE_THERMO_PHASE[phase_role_token])\n",
            "            if phase_raw is None or phase_raw == \"\":\n"
            "                assert phase_role_token is not None\n"
            "                phase = State.of(_PHASE_ROLE_THERMO_PHASE[phase_role_token])\n",
        )
    elif mutation == "alloy-capability":
        replace_once(
            SCORE,
            "    if role is PhaseRole.METAL:\n        from simulator.melt_backend.base import battery_adapter_class\n",
            "    if role is PhaseRole.METAL and not (\n"
            "        composition.amount_basis is AmountBasis.MASS_PERCENT\n"
            "        and sum(\n"
            "            (amount for _name, amount in composition.components), Decimal(0)\n"
            "        ) != Decimal(\"100\")\n"
            "    ):\n"
            "        from simulator.melt_backend.base import battery_adapter_class\n",
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

        test_path = TEST_BY_MUTATION[args.mutation]
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "4",
            "-p",
            "no:cacheprovider",
            str(test_path),
        ]

        def run_test() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        try:
            baseline = run_test()
            if baseline.returncode != 0:
                print(
                    f"unmodified regression test failed for {args.mutation}:\n"
                    f"{baseline.stdout}{baseline.stderr}",
                    file=sys.stderr,
                )
                return 1
            print(f"unmodified regression test passed: {test_path.name}")

            restore(args.mutation)
            result = run_test()
            output = result.stdout + result.stderr
            if result.returncode == 0:
                print(f"mutation survived: {args.mutation}", file=sys.stderr)
                return 1
            expected = EXPECTED_FAILURE[args.mutation]
            failures = [
                line for line in output.splitlines() if line.startswith("FAILED ")
            ]
            errors = [
                line for line in output.splitlines() if line.startswith("ERROR ")
            ]
            if (
                len(failures) != 1
                or errors
                or EXPECTED_NODE[args.mutation] not in failures[0]
                or expected not in failures[0]
            ):
                print(
                    f"mutation failed for an unexpected reason: {args.mutation}; "
                    f"expected only {EXPECTED_NODE[args.mutation]} to fail with "
                    f"{expected!r}\n{output}",
                    file=sys.stderr,
                )
                return 1
            print(f"mutation detected: {args.mutation}")
            return 0
        finally:
            for path, backup in backups.items():
                path.write_bytes(backup.read_bytes())


if __name__ == "__main__":
    raise SystemExit(main())
