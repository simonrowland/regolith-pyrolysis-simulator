"""Black-box checks for per-test CPU budgets and wall hang guards."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_probe(
    tmp_path: Path,
    source: str,
    *,
    expected_test_count: int,
    junit_xml: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    test_file = tmp_path / "test_cpu_budget_probe.py"
    test_file.write_text(textwrap.dedent(source), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-v",
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
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = _output(result)
    assert "ERROR collecting" not in output, output
    assert re.search(rf"collected {expected_test_count} items?\b", output), output
    return result


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
        expected_test_count=1,
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
        expected_test_count=1,
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
        expected_test_count=1,
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
        expected_test_count=2,
    )

    assert result.returncode == pytest.ExitCode.TESTS_FAILED
    assert "CPU budget exceeded:" in _output(result)
    assert next_test_ran.read_text(encoding="utf-8") == "ran"


def test_wall_guard_scales_with_cpu_budget() -> None:
    from tests._cpu_budget import wall_guard_seconds

    assert wall_guard_seconds(1) == 6
    assert wall_guard_seconds(300) == 1800


def test_junit_xml_records_cpu_and_wall_seconds(tmp_path: Path) -> None:
    junit_xml = tmp_path / "junit.xml"
    result = _run_probe(
        tmp_path,
        """
        import time
        import pytest

        @pytest.mark.cpu_budget(2)
        def test_receipt():
            time.sleep(0.05)
        """,
        expected_test_count=1,
        junit_xml=junit_xml,
    )

    assert result.returncode == pytest.ExitCode.OK, _output(result)
    testcase = ET.parse(junit_xml).getroot().find(".//testcase")
    assert testcase is not None
    properties_node = testcase.find("properties")
    assert properties_node is not None
    properties = {
        prop.attrib["name"]: float(prop.attrib["value"])
        for prop in properties_node.findall("property")
    }
    assert properties["cpu_seconds"] >= 0.0
    assert properties["wall_seconds"] >= 0.0
