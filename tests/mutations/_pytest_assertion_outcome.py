"""Outcome checks shared by focused pytest mutation scripts."""

from __future__ import annotations

import re


_FAILURE_HEADER = re.compile(r"_{3,}\s+(.+?)\s+_{3,}")
_SHORT_SUMMARY = re.compile(r"=+\s*short test summary info\s*=+", re.IGNORECASE)
_OUTCOME_COUNT = re.compile(
    r"\b(\d+)\s+(failed|passed|errors?|skipped|xfailed|xpassed|warnings?|deselected)\b",
    re.IGNORECASE,
)


def pytest_assertion_failed(
    *,
    returncode: int,
    output: str,
    node_id: str,
    expected_reason: str,
) -> bool:
    """Return whether pytest ran this node and reported its expected failure."""
    lowered_output = output.lower()
    if returncode != 1 or "no tests ran" in lowered_output:
        return False
    if "error collecting" in lowered_output:
        return False

    lines = output.splitlines()
    summary_prefix = f"FAILED {node_id}"
    failed_lines = [
        line.strip() for line in lines if line.strip().startswith("FAILED ")
    ]
    if len(failed_lines) != 1:
        return False
    if not (
        failed_lines[0] == summary_prefix
        or failed_lines[0].startswith(f"{summary_prefix} - ")
    ):
        return False

    result_lines = [
        _OUTCOME_COUNT.findall(line)
        for line in lines
        if _OUTCOME_COUNT.search(line)
    ]
    if result_lines != [[("1", "failed")]]:
        return False

    target_name = node_id.rsplit("::", 1)[-1]
    for index, line in enumerate(lines):
        header = _FAILURE_HEADER.fullmatch(line)
        if header is None or header.group(1).strip() != target_name:
            continue

        section = []
        for section_line in lines[index + 1 :]:
            if _FAILURE_HEADER.fullmatch(section_line) or _SHORT_SUMMARY.fullmatch(
                section_line.strip()
            ):
                break
            section.append(section_line)

        if any(
            re.match(r"^E\s+", section_line) and expected_reason in section_line
            for section_line in section
        ):
            return True
    return False
