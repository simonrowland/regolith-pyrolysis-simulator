"""Run the four chunk 7 regression mutations and restore core.py each time."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "simulator" / "core.py"
MERGE_COMMIT = "385db7dd176ab14e58b362fd37d8eec2f18bc691"
PYTEST = [
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "--tb=short",
    "--timeout=600",
    "-n",
    "0",
]


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    occurrences = source.count(old)
    if occurrences != 1:
        raise RuntimeError(
            f"{label}: expected one mutation point, found {occurrences}"
        )
    return source.replace(old, new, 1)


def _method_source(source: str) -> tuple[int, int, str]:
    start_match = re.search(
        r"^    def _oxygen_shadow_transfer\(\n",
        source,
        flags=re.MULTILINE,
    )
    if start_match is None:
        raise RuntimeError("could not locate _oxygen_shadow_transfer")
    next_method = re.search(
        r"^    def [A-Za-z_][A-Za-z0-9_]*\(",
        source[start_match.end() :],
        flags=re.MULTILINE,
    )
    if next_method is None:
        raise RuntimeError("could not locate the method after _oxygen_shadow_transfer")
    end = start_match.end() + next_method.start()
    return start_match.start(), end, source[start_match.start() : end]


def _run_expected_failure(
    *,
    label: str,
    mutated_source: str,
    node_id: str,
    expected_output: str,
) -> None:
    original_bytes = CORE_PATH.read_bytes()
    try:
        CORE_PATH.write_text(mutated_source, encoding="utf-8")
        result = subprocess.run(
            [*PYTEST, node_id],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        transcript = result.stdout + result.stderr
        if result.returncode == 0:
            raise RuntimeError(f"{label}: mutation unexpectedly passed")
        if node_id not in transcript:
            raise RuntimeError(
                f"{label}: pytest did not report the intended case\n{transcript}"
            )
        if expected_output not in transcript:
            raise RuntimeError(
                f"{label}: failure did not reach its regression assertion "
                f"({expected_output!r})\n{transcript}"
            )
        print(f"detected: {label} -> {node_id}")
    finally:
        CORE_PATH.write_bytes(original_bytes)
        if CORE_PATH.read_bytes() != original_bytes:
            raise RuntimeError(f"{label}: failed to restore simulator/core.py")


def _legacy_method() -> str:
    result = subprocess.run(
        ["git", "show", f"{MERGE_COMMIT}:simulator/core.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    _start, _end, method = _method_source(result.stdout)
    if "for _ in range(substeps)" not in method or "for _ in range(80)" not in method:
        raise RuntimeError("merge commit no longer contains the nested amount solver")
    return method


def main() -> None:
    original_bytes = CORE_PATH.read_bytes()
    original = original_bytes.decode("utf-8")

    exponential = """                    coefficient_s = (
                        -math.expm1(-relaxation_rate * step_s)
                        / relaxation_rate
                    )"""
    backward_euler = """                    coefficient_s = (
                        step_s / (1.0 + relaxation_rate * step_s)
                    )"""
    be_source = _replace_once(
        original,
        exponential,
        backward_euler,
        "backward-Euler coefficient",
    )
    _run_expected_failure(
        label="backward-Euler coefficient",
        mutated_source=be_source,
        node_id=(
            "tests/test_redox_authority_floor.py::"
            "test_exponential_linear_relaxation_is_exact[release-0.01]"
        ),
        expected_output="FAILED",
    )

    start, end, _current_method = _method_source(original)
    nested_loop_source = (
        original[:start] + _legacy_method() + original[end:]
    )
    _run_expected_failure(
        label="nested substep and amount-bisection loop",
        mutated_source=nested_loop_source,
        node_id=(
            "tests/test_redox_authority_floor.py::"
            "test_exponential_linear_relaxation_is_exact[release-1.0]"
        ),
        expected_output="interface_evaluations",
    )

    convergence_guard = "if root.get('interface_root_converged') is not True:"
    no_convergence_guard = _replace_once(
        original,
        convergence_guard,
        "if False:",
        "interface convergence guard",
    )
    _run_expected_failure(
        label="ignored interface convergence flag",
        mutated_source=no_convergence_guard,
        node_id=(
            "tests/test_redox_authority_floor.py::"
            "test_exponential_interface_root_miss_is_predicted_and_flagged"
        ),
        expected_output="assert any",
    )

    no_exhaustion_notice = _replace_once(
        original,
        "            if accepted is None and solver_failure is None:\n",
        "            if False:\n",
        "refinement exhaustion notice",
    )
    _run_expected_failure(
        label="unflagged refinement exhaustion",
        mutated_source=no_exhaustion_notice,
        node_id=(
            "tests/test_redox_authority_floor.py::"
            "test_exponential_refinement_exhaustion_is_predicted_and_flagged"
        ),
        expected_output="assert any",
    )

    if CORE_PATH.read_bytes() != original_bytes:
        raise RuntimeError("final simulator/core.py differs from its starting bytes")
    print("all mutations restored")


if __name__ == "__main__":
    main()
