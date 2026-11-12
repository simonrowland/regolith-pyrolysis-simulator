from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_ID_SIMULATOR_CEILING = 25
SOURCE_ID_TOOLS_SCRIPTS_CEILING = 2
FINITE_HELPER_CEILING = 87
SC_388_SITE_CEILING = 5

SC_388_ALLOWLIST = (
    (
        "simulator/diagnostic_helpers/species_rail_differential.py",
        "_evaluate_rail_pressure_Pa",
        "engine",
        "d-396 #1",
    ),
    (
        "engines/alphamelts/provider.py",
        "_run_liquidus_finder",
        "engine",
        "d-396 #2",
    ),
    (
        "simulator/furnace_materials.py",
        "_catalog_max_service_T_C",
        "physics",
        "d-396 #5",
    ),
    (
        "scripts/render_flowsheet.py",
        "build_live_major_facts",
        "physics",
        "d-396 #6",
    ),
    ("simulator/run_executor.py", "_safe_str", "engine", "d-396 #11"),
)


def _git_grep(pattern: str, *paths: str) -> list[str]:
    completed = subprocess.run(
        ["git", "grep", "-I", "-n", "-E", pattern, "--", *paths],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode in {0, 1}, completed.stderr
    return completed.stdout.splitlines()


def _assert_count_at_most(
    label: str, matches: list[str], ceiling: int
) -> None:
    extra = len(matches) - ceiling
    assert extra <= 0, (
        f"{label} gained {extra} new/extra matching line(s) above ceiling "
        f"{ceiling}; matching lines:\n" + "\n".join(matches)
    )


def test_source_id_comparisons_are_decrease_only() -> None:
    simulator = _git_grep(
        r'source_id[[:space:]]*(==|!=)[[:space:]]*"', "simulator"
    )
    tools_scripts = _git_grep(
        r'source_id[[:space:]]*(==|!=)[[:space:]]*"', "tools", "scripts"
    )

    _assert_count_at_most(
        "source_id comparisons in simulator", simulator, SOURCE_ID_SIMULATOR_CEILING
    )
    _assert_count_at_most(
        "source_id comparisons in tools/scripts",
        tools_scripts,
        SOURCE_ID_TOOLS_SCRIPTS_CEILING,
    )


def test_finite_numeric_helpers_are_frozen() -> None:
    matches = _git_grep(
        r"^[[:space:]]*def (_finite|_is_number)",
        "simulator",
        "engines",
        "tools",
        "scripts",
        "web",
    )
    _assert_count_at_most(
        "finite/_is_number helpers", matches, FINITE_HELPER_CEILING
    )


def test_sc388_allowlist_only_shrinks() -> None:
    entries = [
        f"{path}:{function} ({lane}, {task})"
        for path, function, lane, task in SC_388_ALLOWLIST
    ]
    extra = len(entries) - SC_388_SITE_CEILING
    assert extra <= 0, (
        f"SC-388 allow-list gained {extra} new/extra site(s) above ceiling "
        f"{SC_388_SITE_CEILING}: " + "; ".join(entries)
    )

    for path, function, lane, task in SC_388_ALLOWLIST:
        definition = _git_grep(
            rf"^[[:space:]]*def {re.escape(function)}\(", path
        )
        assert len(definition) == 1, (
            f"SC-388 site {path}:{function} ({lane}, {task}) no longer exists; "
            "remove its fixed entry from the allow-list"
        )
