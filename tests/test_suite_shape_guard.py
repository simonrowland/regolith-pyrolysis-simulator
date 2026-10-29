"""Black-box regression tests for the suite-shape collection plugin."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

pytest_plugins = ("pytester",)


def _make_guard_suite(pytester, *, source: str, roster: tuple[tuple[str, str], ...]):
    tests_dir = pytester.path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_synthetic.py").write_text(source, encoding="utf-8")
    subdirectory = tests_dir / "sub"
    subdirectory.mkdir()
    (subdirectory / "test_subdirectory.py").write_text(
        "def test_subdirectory_node():\n    pass\n",
        encoding="utf-8",
    )
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
        "    cpu_budget(seconds): synthetic CPU budget\n"
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


@pytest.mark.parametrize(
    "collection_args",
    [
        pytest.param(("tests/", "tests/sub/"), id="tests-plus-subdirectory"),
        pytest.param((".",), id="repository-root"),
        pytest.param((), id="configured-testpaths"),
        pytest.param(
            ("tests/", "tests/test_synthetic.py::test_unrostered_heavy_node"),
            id="tests-plus-node-id",
        ),
    ],
)
def test_suite_shape_collection_audit_catches_deselected_phantom_and_unrostered_heavy(
    pytester, collection_args: tuple[str, ...],
) -> None:
    _make_guard_suite(
        pytester,
        roster=(("tests/test_synthetic.py", "test_phantom_node"),),
        source=textwrap.dedent(
            """\
            import pytest

            @pytest.mark.nightly
            @pytest.mark.xdist_group("magemin_fullrun_a")
            @pytest.mark.cpu_budget(600)
            def test_unrostered_heavy_node():
                pass
            """
        ),
    )

    result = pytester.runpytest_subprocess(
        "--collect-only",
        "-m",
        "not nightly",
        *collection_args,
    )

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        [
            "*suite-shape collection audit failed:*",
            "*test_unrostered_heavy_node*not in the shrink-only heavy roster*",
            "*roster entry ('tests/test_synthetic.py', 'test_phantom_node') matches no collected test*",
        ]
    )


@pytest.mark.parametrize(
    "run_args",
    [
        pytest.param(
            ("-k", "real_node", "tests/test_synthetic.py"),
            id="focused-file-and-k",
        ),
        pytest.param(
            ("tests/test_synthetic.py::test_real_node",),
            id="focused-node-id",
        ),
        pytest.param(("tests/sub/",), id="focused-subdirectory"),
    ],
)
def test_suite_shape_collection_audit_skips_phantoms_for_focused_run(
    pytester, run_args: tuple[str, ...],
) -> None:
    _make_guard_suite(
        pytester,
        roster=(("tests/test_synthetic.py", "test_phantom_node"),),
        source="def test_real_node():\n    pass\n",
    )

    result = pytester.runpytest_subprocess("--collect-only", *run_args)

    assert result.ret == pytest.ExitCode.OK
    assert "phantom row" not in result.stderr.str()


def test_suite_shape_uses_cpu_budget_instead_of_wall_timeout(pytester) -> None:
    _make_guard_suite(
        pytester,
        roster=(),
        source=textwrap.dedent(
            """\
            import pytest

            @pytest.mark.timeout(900)
            def test_wall_guard_override():
                pass
            """
        ),
    )

    result = pytester.runpytest_subprocess(
        "--collect-only",
        "tests/test_synthetic.py",
    )

    assert result.ret == pytest.ExitCode.OK, result.stderr.str()


def test_suite_shape_collection_audit_skips_phantoms_for_dot_from_subdirectory(
    pytester, monkeypatch,
) -> None:
    _make_guard_suite(
        pytester,
        roster=(("tests/test_synthetic.py", "test_phantom_node"),),
        source="def test_real_node():\n    pass\n",
    )
    monkeypatch.chdir(pytester.path / "tests" / "sub")

    result = pytester.runpytest_subprocess("--collect-only", ".")

    assert result.ret == pytest.ExitCode.OK
    assert "phantom row" not in result.stderr.str()
