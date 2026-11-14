#!/usr/bin/env python3
"""Break two t-1609 pins and require the covering tests to fail.

The oxygen-demand line charges z_i/2. Rewriting that divisor to z_i/1 must
turn the hand-arithmetic excess tests red. The circularity guard leaves a
set refusal as a refusal. Deleting that guard so the override wins must turn
the out-of-range and missing-oxygen engine-call tests red.

The interpreter is this process. Scratch and library paths come from the
environment. The source is restored after each mutation.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SEAT = Path(__file__).resolve().parents[2]
SOURCE = SEAT / "simulator" / "battery" / "composition.py"
DEMAND_MARKER = (
    "oxygen_demand += cation_moles * Decimal(row.valence) / Decimal(2)"
    "  # t-1609: z_i / 2"
)
DEMAND_BROKEN = (
    "oxygen_demand += cation_moles * Decimal(row.valence) / Decimal(1)"
    "  # t-1609: z_i / 2"
)
DEMAND_SELECT = (
    "hand_example_relabels or extra_oxygen_mass_fraction "
    "or boundaries_are_all_ferrous or below_zero_and_above "
    "or titanium_manganese"
)
REFUSAL_MARKER = (
    "if refusal is None and _is_circular_observable(observable) and oxide_moles:"
)
REFUSAL_BROKEN = (
    "if _is_circular_observable(observable) and oxide_moles:"
)
REFUSAL_SELECT = (
    "out_of_range_circular_row_does_not_call_the_engine "
    "or missing_oxygen_circular_row_does_not_call_the_engine"
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SEAT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _mutate(original: str, marker: str, broken: str, select: str, label: str) -> str:
    count = original.count(marker)
    if count != 1:
        print(f"{label}: marker count is {count}, expected 1", file=sys.stderr)
        return "marker"
    mutated = original.replace(marker, broken, 1)
    if mutated == original or broken not in mutated:
        print(f"{label}: replacement did not change the source", file=sys.stderr)
        return "marker"
    SOURCE.write_text(mutated, encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-n",
                "0",
                "-p",
                "no:cacheprovider",
                "tests/battery/test_element_wt_percent_measured_oxygen.py",
                "-k",
                select,
            ],
            cwd=SEAT,
            env=_env(),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        SOURCE.write_text(original, encoding="utf-8")
    restored = SOURCE.read_text(encoding="utf-8")
    if restored != original or broken in restored or restored.count(marker) != 1:
        print(f"{label}: source was not restored", file=sys.stderr)
        return "restore"
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    if completed.returncode == 0 or "FAILED" not in completed.stdout + completed.stderr:
        print(
            f"{label}: mutation left the tests green; the pin does not cover it",
            file=sys.stderr,
        )
        return "green"
    print(
        f"{label}: mutation failed the tests (pytest exit {completed.returncode}) "
        "and the source was restored"
    )
    return "red"


def main() -> int:
    original = SOURCE.read_text(encoding="utf-8")
    demand = _mutate(original, DEMAND_MARKER, DEMAND_BROKEN, DEMAND_SELECT, "z_i/2")
    if demand != "red":
        return 1 if demand == "green" else 2
    refusal = _mutate(
        original, REFUSAL_MARKER, REFUSAL_BROKEN, REFUSAL_SELECT, "refusal override"
    )
    if refusal != "red":
        return 1 if refusal == "green" else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
