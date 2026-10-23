from __future__ import annotations

import os
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SYNTHETIC_ROSTER = Path(__file__).with_name("fixtures") / "known_red_synthetic.toml"
_PLUGIN_AVAILABLE = find_spec("_pytest_known_red") is not None
_SYNTHETIC_TESTS = """\
def test_known_red_failure():
    assert False, "synthetic known red"


def test_known_red_pass():
    assert True


def test_not_on_roster():
    assert True
"""


def _run(
    project_root: Path,
    *,
    known_red_only: bool,
    roster: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "pytest", "-q"]
    if roster is not None and _PLUGIN_AVAILABLE:
        command.extend(("--known-red-roster", str(roster)))
    if known_red_only and _PLUGIN_AVAILABLE:
        command.append("--known-red-only")
    pythonpath = os.pathsep.join(
        filter(None, (str(_REPO_ROOT), os.environ.get("PYTHONPATH", "")))
    )
    return subprocess.run(
        command,
        cwd=project_root,
        env={**os.environ, "PYTHONPATH": pythonpath},
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )


def _make_quarantine_project(project_root: Path) -> None:
    (project_root / "test_quarantine.py").write_text(
        _SYNTHETIC_TESTS,
        encoding="utf-8",
    )
    (project_root / "conftest.py").write_text(
        """\
import importlib.util

pytest_plugins = (
    ("_pytest_known_red",)
    if importlib.util.find_spec("_pytest_known_red") is not None
    else ()
)
""",
        encoding="utf-8",
    )


def test_gating_run_deselects_and_reports_rostered_nodes(tmp_path):
    _make_quarantine_project(tmp_path)

    result = _run(tmp_path, known_red_only=False, roster=_SYNTHETIC_ROSTER)

    assert result.returncode == 0
    assert "1 passed, 2 deselected" in result.stdout
    output = result.stdout + result.stderr
    assert "Known-red quarantine: 2 deselected" in output
    assert "test_quarantine.py::test_known_red_failure" in output
    assert "test_quarantine.py::test_known_red_pass" in output


def test_known_red_run_xfails_strictly_and_surfaces_xpass_and_stale_nodes(
    tmp_path,
):
    _make_quarantine_project(tmp_path)

    result = _run(tmp_path, known_red_only=True, roster=_SYNTHETIC_ROSTER)

    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "1 xfailed" in output
    assert "1 deselected" in output
    assert "XPASS(strict)" in output
    assert "Known-red roster stale node: test_quarantine.py::test_removed" in output
    assert "non-roster test was executed" not in output


def test_malformed_roster_fails_collection_loudly(tmp_path):
    _make_quarantine_project(tmp_path)
    malformed_roster = tmp_path / "malformed-known-red.toml"
    malformed_roster.write_text(
        """
[[test]]
node = "test_quarantine.py::test_known_red_failure"
tier = "pr"
reason = "missing ticket"
since = "7dd268c6f"
""",
        encoding="utf-8",
    )

    result = _run(tmp_path, known_red_only=False, roster=malformed_roster)

    assert result.returncode != 0
    assert "missing required field 'ticket'" in result.stdout + result.stderr


def test_xdist_group_suffixes_match_only_roster_nodes_in_this_collection():
    roster_nodes = (
        "test_quarantine.py::test_known_red_failure",
        "test_quarantine.py::test_removed",
    )
    xdist_nodeids = (
        "test_quarantine.py::test_known_red_failure@magemin_fullrun_a",
        "test_quarantine.py::test_not_on_roster@serial",
    )

    try:
        from _pytest_known_red import _partition_roster_nodes
    except ModuleNotFoundError:
        selected, stale = (), roster_nodes
    else:
        selected, stale = _partition_roster_nodes(roster_nodes, xdist_nodeids)

    assert selected == (roster_nodes[0],)
    assert stale == (roster_nodes[1],)
