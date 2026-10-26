"""Black-box regression tests for the suite-shape collection plugin."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

pytest_plugins = ("pytester",)


def _make_guard_suite(pytester, *, source: str, roster: tuple[tuple[str, str], ...]):
    tests_dir = pytester.path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_synthetic.py").write_text(source, encoding="utf-8")
    (tests_dir / "xdist_loadgroup_durations.json").write_text(
        json.dumps({"durations_seconds": {"magemin_fullrun_a": 1}}),
        encoding="utf-8",
    )
    pytester.makeini(
        "[pytest]\n"
        "testpaths = tests\n"
        "markers =\n"
        "    nightly: synthetic nightly test\n"
        "    serial: synthetic serial test\n"
        "    xdist_group(name): synthetic worker group\n"
        "    timeout(seconds): synthetic timeout\n"
    )
    repo_root = Path(__file__).resolve().parents[1]
    pytester.makeconftest(
        "import sys\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from pathlib import Path\n"
        "from tests import suite_shape_guard\n"
        f"suite_shape_guard.HEAVY_ROSTER = frozenset({roster!r})\n"
        "suite_shape_guard.DURATION_HINTS_PATH = (\n"
        "    Path.cwd() / 'tests' / 'xdist_loadgroup_durations.json'\n"
        ")\n"
        "pytest_plugins = ('tests.suite_shape_guard',)\n"
    )


def test_suite_shape_collection_audit_catches_deselected_phantom_and_unrostered_heavy(
    pytester,
) -> None:
    _make_guard_suite(
        pytester,
        roster=(("tests/test_synthetic.py", "test_phantom_node"),),
        source=textwrap.dedent(
            """\
            import pytest

            @pytest.mark.nightly
            @pytest.mark.xdist_group("magemin_fullrun_a")
            @pytest.mark.timeout(600)
            def test_unrostered_heavy_node():
                pass
            """
        ),
    )

    result = pytester.runpytest_subprocess(
        "--collect-only",
        "-m",
        "not serial and not nightly",
        "tests/",
    )

    output = result.stdout.str()
    assert result.ret != 0
    assert "phantom row" in output
    assert "test_unrostered_heavy_node" in output
    assert "not in the shrink-only heavy roster" in output


def test_suite_shape_collection_audit_skips_phantoms_for_focused_k_run(
    pytester,
) -> None:
    _make_guard_suite(
        pytester,
        roster=(("tests/test_synthetic.py", "test_phantom_node"),),
        source="def test_real_node():\n    pass\n",
    )

    result = pytester.runpytest_subprocess(
        "--collect-only",
        "-k",
        "real_node",
        "tests/test_synthetic.py",
    )

    output = result.stdout.str()
    assert result.ret == 0
    assert "phantom row" not in output
