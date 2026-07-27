from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.generate_xdist_loadgroup_hints import build_duration_hints


REPO_ROOT = Path(__file__).resolve().parents[1]
FLAGGED_GROUPS = (
    "magemin_fullrun_c",
    "magemin_fullrun_a",
    "magemin_fullrun_b",
    "serial",
)


def _run_pytest(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(REPO_ROOT), env.get("PYTHONPATH")))
    )
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_harness_conftest(path: Path) -> None:
    (path / "conftest.py").write_text(
        'pytest_plugins = ("_pytest_loadgroup_order",)\n',
        encoding="utf-8",
    )


def test_collection_order_is_duration_sorted_and_deterministic(tmp_path: Path) -> None:
    _write_harness_conftest(tmp_path)
    (tmp_path / "test_order.py").write_text(
        """
import pytest

def test_unknown(): pass
def test_unknown_2(): pass

@pytest.mark.xdist_group("magemin_fullrun_b")
def test_b(): pass

@pytest.mark.xdist_group("magemin_fullrun_c")
def test_c(): pass

@pytest.mark.xdist_group("serial")
def test_serial(): pass

@pytest.mark.xdist_group("magemin_fullrun_a")
def test_a(): pass
""",
        encoding="utf-8",
    )

    commands = ("-n0", "--dist", "loadgroup", "--collect-only", "-q")
    first = _run_pytest(tmp_path, *commands)
    second = _run_pytest(tmp_path, *commands)
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr

    def collected(result: subprocess.CompletedProcess[str]) -> list[str]:
        return [
            line.rsplit("::", 1)[-1]
            for line in result.stdout.splitlines()
            if line.startswith("test_order.py::")
        ]

    expected = [
        "test_c",
        "test_a",
        "test_b",
        "test_serial",
        "test_unknown",
        "test_unknown_2",
    ]
    assert collected(first) == expected
    assert collected(second) == expected


def test_project_default_preserves_collection_duration_order(pytestconfig) -> None:
    assert pytestconfig.getoption("loadscopereorder") is False


def test_loadgroup_first_wave_starts_flagged_chains(tmp_path: Path) -> None:
    _write_harness_conftest(tmp_path)
    marker_dir = tmp_path / "starts"
    (tmp_path / "test_starts.py").write_text(
        f"""
import os
import time
from pathlib import Path

import pytest

FLAGGED = {FLAGGED_GROUPS!r}
MARKERS = Path(os.environ["LOADGROUP_START_MARKERS"])

def record_start(group):
    MARKERS.mkdir(exist_ok=True)
    (MARKERS / group).touch()
    deadline = time.monotonic() + 10
    if group in FLAGGED:
        while time.monotonic() < deadline:
            if all((MARKERS / name).exists() for name in FLAGGED):
                return
            time.sleep(0.01)
        pytest.fail("flagged first wave did not all start")
    assert all((MARKERS / name).exists() for name in FLAGGED)

def test_unknown_1(): record_start("unknown_1")
def test_unknown_2(): record_start("unknown_2")
def test_unknown_3(): record_start("unknown_3")
def test_unknown_4(): record_start("unknown_4")

@pytest.mark.xdist_group("magemin_fullrun_b")
def test_b(): record_start("magemin_fullrun_b")

@pytest.mark.xdist_group("magemin_fullrun_c")
def test_c(): record_start("magemin_fullrun_c")

@pytest.mark.xdist_group("serial")
def test_serial(): record_start("serial")

@pytest.mark.xdist_group("magemin_fullrun_a")
def test_a(): record_start("magemin_fullrun_a")
""",
        encoding="utf-8",
    )
    env_marker = os.environ.get("LOADGROUP_START_MARKERS")
    os.environ["LOADGROUP_START_MARKERS"] = str(marker_dir)
    try:
        result = _run_pytest(
            tmp_path,
            "-n4",
            "--dist",
            "loadgroup",
            "--no-loadscope-reorder",
            "-q",
        )
    finally:
        if env_marker is None:
            os.environ.pop("LOADGROUP_START_MARKERS", None)
        else:
            os.environ["LOADGROUP_START_MARKERS"] = env_marker
    assert result.returncode == 0, result.stdout + result.stderr


def test_duration_hints_use_median_of_per_suite_chain_totals(tmp_path: Path) -> None:
    paths = []
    for index, durations in enumerate(((10, 20), (30, 40), (50, 60))):
        path = tmp_path / f"suite-{index}.xml"
        path.write_text(
            (
                "<testsuite>"
                f'<testcase name="one@magemin_fullrun_c" time="{durations[0]}"/>'
                f'<testcase name="two@magemin_fullrun_c" time="{durations[1]}"/>'
                '<testcase name="param[value@host]" time="999"/>'
                "</testsuite>"
            ),
            encoding="utf-8",
        )
        paths.append(path)

    assert build_duration_hints(paths) == {"magemin_fullrun_c": 70.0}
