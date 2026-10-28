"""Black-box checks for per-test CPU budgets and wall hang guards."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_probe(
    tmp_path: Path,
    source: str,
    *,
    junit_xml: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    test_file = tmp_path / "test_cpu_budget_probe.py"
    test_file.write_text(source, encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "conftest",
        "-c",
        str(REPO_ROOT / "pyproject.toml"),
        "-o",
        "addopts=",
    ]
    if junit_xml is not None:
        command.extend(("--junitxml", str(junit_xml)))
    command.append(str(test_file))
    environment = {**os.environ, "PYTEST_ADDOPTS": ""}
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def test_busy_loop_exceeding_cpu_budget_fails_with_cpu_message(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        """
        import pytest

        @pytest.mark.cpu_budget(1)
        def test_busy_loop_exceeds_budget():
            while True:
                pass
        """,
    )

    assert result.returncode == pytest.ExitCode.TESTS_FAILED
    assert "CPU budget exceeded:" in _output(result)
    assert " s of 1 s" in _output(result)


def test_sleep_over_cpu_budget_wall_time_passes(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        """
        import time
        import pytest

        @pytest.mark.cpu_budget(1)
        def test_sleep_uses_little_cpu():
            time.sleep(1.25)
        """,
    )

    assert result.returncode == pytest.ExitCode.OK, _output(result)


def test_sleep_over_wall_guard_fails_with_hang_message(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        """
        import time
        import pytest

        @pytest.mark.cpu_budget(5)
        @pytest.mark.timeout(1)
        def test_sleep_exceeds_wall_guard():
            time.sleep(5)
        """,
    )

    assert result.returncode == pytest.ExitCode.TESTS_FAILED
    assert "wall-clock hang guard" in _output(result)


def test_cpu_limit_is_restored_after_failure_for_next_test(tmp_path: Path) -> None:
    next_test_ran = tmp_path / "next-test-ran"
    result = _run_probe(
        tmp_path,
        f"""
        from pathlib import Path
        import pytest

        @pytest.mark.cpu_budget(1)
        def test_00_busy_loop_fails():
            while True:
                pass

        def test_01_next_test_runs():
            Path({str(next_test_ran)!r}).write_text("ran", encoding="utf-8")
        """,
    )

    assert result.returncode == pytest.ExitCode.TESTS_FAILED
    assert "CPU budget exceeded:" in _output(result)
    assert next_test_ran.read_text(encoding="utf-8") == "ran"


def test_wall_guard_scales_with_cpu_budget() -> None:
    from tests._cpu_budget import wall_guard_seconds

    assert wall_guard_seconds(1) == 6
    assert wall_guard_seconds(300) == 1800
