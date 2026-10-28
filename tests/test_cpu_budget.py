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
    xdist_workers: int | None = None,
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
    if xdist_workers is not None:
        command.extend(("-n", str(xdist_workers)))
    if junit_xml is not None:
        command.extend(("--junitxml", str(junit_xml)))
    command.append(str(test_file))
    environment = {**os.environ, "PYTEST_ADDOPTS": ""}
    if xdist_workers is not None:
        for key in tuple(environment):
            if key.startswith("PYTEST_XDIST_"):
                environment.pop(key)
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
    collection_count = re.search(
        rf"collected {expected_test_count} items?\b", output
    ) or re.search(
        rf"\b\d+ workers? \[{expected_test_count} items?\]", output
    )
    assert collection_count is not None, output
    return result


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


@pytest.mark.parametrize(
    "xdist_workers",
    [pytest.param(None, id="serial"), pytest.param(2, id="xdist-2")],
)
def test_busy_loop_exceeding_cpu_budget_fails_with_cpu_message(
    tmp_path: Path,
    xdist_workers: int | None,
) -> None:
    result = _run_probe(
        tmp_path,
        """
        import time
        import pytest

        @pytest.mark.cpu_budget(0.1)
        @pytest.mark.timeout(5)
        def test_busy_loop_exceeds_budget():
            started = time.process_time()
            while time.process_time() - started < 0.3:
                pass
        """,
        expected_test_count=1,
        xdist_workers=xdist_workers,
    )

    assert result.returncode == pytest.ExitCode.TESTS_FAILED
    assert "CPU budget exceeded:" in _output(result)
    assert " s of 0.1 s" in _output(result)


def test_sleep_over_cpu_budget_wall_time_passes(tmp_path: Path) -> None:
    result = _run_probe(
        tmp_path,
        """
        import time
        import pytest

        @pytest.mark.cpu_budget(0.1)
        @pytest.mark.timeout(5)
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


def test_reaping_earlier_cpu_child_does_not_charge_its_cpu_to_this_test(
    tmp_path: Path,
) -> None:
    result = _run_probe(
        tmp_path,
        """
        import subprocess
        import pytest
        import sys

        burn_cpu = "\\n".join((
            "import time",
            "started = time.process_time()",
            "while time.process_time() - started < 0.35:",
            "    pass",
        ))
        child = None

        @pytest.mark.cpu_budget(0.5)
        @pytest.mark.timeout(5)
        def test_00_start_cpu_child():
            global child
            child = subprocess.Popen([sys.executable, "-c", burn_cpu])

        @pytest.mark.cpu_budget(0.1)
        @pytest.mark.timeout(5)
        def test_01_reap_earlier_child():
            assert child is not None
            assert child.wait(timeout=5) == 0
        """,
        expected_test_count=2,
    )

    output = _output(result)
    assert result.returncode == pytest.ExitCode.OK, output


def test_long_lived_child_is_not_limited_by_the_next_test(tmp_path: Path) -> None:
    child_limits = tmp_path / "child-limits.json"
    child_finished = tmp_path / "child-finished"
    result = _run_probe(
        tmp_path,
        f"""
        import json
        import resource
        import subprocess
        import sys
        import time
        from pathlib import Path
        import pytest

        initial_limits = list(resource.getrlimit(resource.RLIMIT_CPU))
        limits_file = Path({str(child_limits)!r})
        finished_file = Path({str(child_finished)!r})
        child_source = "\\n".join((
            "import json, resource, sys, time",
            "from pathlib import Path",
            "limits, finished = map(Path, sys.argv[1:])",
            "limits.write_text(json.dumps(resource.getrlimit(resource.RLIMIT_CPU)))",
            "time.sleep(1.0)",
            "finished.write_text('done')",
        ))
        child = None

        @pytest.mark.cpu_budget(0.2)
        @pytest.mark.timeout(5)
        def test_00_spawn_long_lived_child():
            global child
            child = subprocess.Popen([
                sys.executable, "-c", child_source,
                str(limits_file), str(finished_file),
            ])
            deadline = time.monotonic() + 3
            while not limits_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert limits_file.exists()
            assert json.loads(limits_file.read_text()) == initial_limits

        @pytest.mark.cpu_budget(0.2)
        @pytest.mark.timeout(5)
        def test_01_child_survives_next_test():
            assert child is not None
            assert child.wait(timeout=3) == 0
            assert finished_file.read_text() == "done"
        """,
        expected_test_count=2,
    )

    assert result.returncode == pytest.ExitCode.OK, _output(result)


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
