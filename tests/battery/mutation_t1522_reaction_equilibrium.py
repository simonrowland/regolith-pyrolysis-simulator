"""Run reversible mutations for the reaction-K identity, ingestion, and score guards."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _expect_mutation_failure(
    *,
    path: Path,
    before: str,
    after: str,
    test: str,
    label: str,
) -> None:
    original = path.read_text(encoding="utf-8")
    if original.count(before) != 1:
        raise RuntimeError(f"expected one {label} guard in {path}")
    path.write_text(original.replace(before, after), encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-n", "0", test],
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0 or f"FAILED {test}" not in output:
            raise RuntimeError(
                f"{label} mutation did not fail its matching test:\n{output}"
            )
        print(f"PASS {label}: reverting the guard fails {test.rsplit('::', 1)[-1]}")
    finally:
        path.write_text(original, encoding="utf-8")


def main() -> None:
    test = "tests/battery/test_reaction_equilibrium_quantity.py"
    _expect_mutation_failure(
        path=ROOT / "simulator/battery/identity.py",
        before=(
            "if q is Quantity.LOG10_K_REACTION:\n"
            "        req(\"reaction\", \"temperature_K\")"
        ),
        after=(
            "if q is Quantity.LOG10_KF:\n"
            "        req(\"reaction\", \"temperature_K\")"
        ),
        test=(
            f"{test}::"
            "test_reaction_identity_includes_term_standard_state_and_is_not_log10_kf"
        ),
        label="reaction identity profile",
    )
    _expect_mutation_failure(
        path=ROOT / "simulator/battery/score.py",
        before="return quantity is Quantity.LOG10_K_REACTION",
        after="return quantity is Quantity.LOG10_KF",
        test=(
            f"{test}::"
            "test_reaction_equilibrium_scoring_refuses_without_dropping_source_value"
        ),
        label="reaction-K scoring refusal",
    )
    _expect_mutation_failure(
        path=ROOT / "simulator/battery/migrate.py",
        before="if encoding_reason is not None:",
        after="if False and encoding_reason is not None:",
        test=(
            f"{test}::"
            "test_reaction_constant_rejects_unestablished_log10_encoding"
        ),
        label="reaction-K log-base refusal",
    )
    _expect_mutation_failure(
        path=ROOT / "simulator/battery/migrate.py",
        before=(
            "standard_pressure_values = values\n"
            "        if q_token is Quantity.LOG10_K_REACTION:\n"
            "            standard_pressure_values = {\n"
            "                key: values[key]\n"
            "                for key in (\"reference_pressure_Pa\", \"standard_pressure_Pa\")\n"
            "                if key in values\n"
            "            }"
        ),
        after=(
            "standard_pressure_values = values\n"
            "        if False and q_token is Quantity.LOG10_K_REACTION:\n"
            "            standard_pressure_values = {\n"
            "                key: values[key]\n"
            "                for key in (\"reference_pressure_Pa\", \"standard_pressure_Pa\")\n"
            "                if key in values\n"
            "            }"
        ),
        test=(
            f"{test}::"
            "test_experimental_pressure_does_not_fill_gas_standard_pressure"
        ),
        label="reaction-K standard pressure source",
    )
    _expect_mutation_failure(
        path=ROOT / "simulator/battery/records.py",
        before="if operator_semantics != (",
        after="if False and operator_semantics != (",
        test=(
            f"{test}::"
            "test_bound_rejects_conflicting_comparator_and_direction_in_codec"
        ),
        label="bound comparator consistency",
    )


if __name__ == "__main__":
    main()
