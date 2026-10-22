#!/usr/bin/env python3
"""Compare outcomes in two junit XML files.

Each testcase is ``classname::name``. The outcome is ``error``, ``failed``,
``skipped``, or ``passed``, using the worst junit child tag present. A case
with no failure, error, or skipped child is passed.

The report has three buckets:

- new fails: the tip is failed or error, and the base case was missing,
  passed, or skipped
- new passes: the tip is passed, and the base case was failed, error, or
  skipped
- changed outcomes: every other outcome transition, including a case
  present on only one side, plus a failure or error whose normalized
  message text differs

Message comparison redacts volatile path prefixes, ``ci-jobs`` run names,
hex addresses, pytest cache and xdist worker ids, and durations ending in
``s``. The report prints the redacted text. Exit status is 0 even when the
tip has new failures; this command reports, it does not gate.

Usage::

    python scripts/junit_compare.py BASE.xml TIP.xml
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, TextIO

# JUnit's child tag is ``failure``; the reported outcome is ``failed``.
_TAG_OUTCOME = {"failure": "failed", "error": "error", "skipped": "skipped"}
_OUTCOME_RANK = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
_FAILING = frozenset({"failed", "error"})
_VOLATILE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/private/var/folders/\S+"), "TMP"),
    (re.compile(r"/private/tmp/\S+"), "TMP"),
    (re.compile(r"/Users/\S+"), "HOME"),
    (re.compile(r"ci-jobs/[\w.-]+"), "ci-jobs/JOB"),
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),
    (re.compile(r"pytest-\d+"), "pytest-N"),
    (re.compile(r"popen-gw\d+"), "popen-gwN"),
    (re.compile(r"\bgw\d+\b"), "gwN"),
    (re.compile(r"\b\d+\.\d+s\b"), "Ns"),
)


@dataclass(frozen=True)
class JunitCase:
    case_id: str
    outcome: str
    message: str


@dataclass(frozen=True)
class OutcomeChange:
    kind: str
    case_id: str
    base_outcome: str
    tip_outcome: str
    base_message: str
    tip_message: str


@dataclass(frozen=True)
class JunitCompare:
    base_cases: int
    tip_cases: int
    base_failing: int
    tip_failing: int
    base_skipped: int
    tip_skipped: int
    changes: tuple[OutcomeChange, ...]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def normalize_junit_message(text: str) -> str:
    """Drop path, address, worker, and timing noise from a failure message."""

    cleaned = text
    for pattern, token in _VOLATILE:
        cleaned = pattern.sub(token, cleaned)
    return "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())


def _message(element: ET.Element) -> str:
    message = element.get("message") or ""
    text = element.text or ""
    return normalize_junit_message(f"{message}\n{text}")


def _case(element: ET.Element) -> JunitCase:
    classname = element.get("classname") or ""
    name = element.get("name") or ""
    outcome = "passed"
    message = ""
    rank = _OUTCOME_RANK[outcome]
    for child in list(element):
        tag = _local(child.tag)
        child_outcome = _TAG_OUTCOME.get(tag)
        if child_outcome is None:
            continue
        child_rank = _OUTCOME_RANK[child_outcome]
        if child_rank >= rank:
            rank = child_rank
            outcome = child_outcome
            message = _message(child)
    return JunitCase(case_id=f"{classname}::{name}", outcome=outcome, message=message)


def load_junit(path: Path) -> dict[str, JunitCase]:
    """Read testcases from a junit XML file. A later duplicate id replaces the earlier one."""

    if not path.is_file():
        raise ValueError(f"not a file: {path}")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    cases: dict[str, JunitCase] = {}
    for element in root.iter():
        if _local(element.tag) != "testcase":
            continue
        case = _case(element)
        cases[case.case_id] = case
    return cases


def _kind(base: JunitCase | None, tip: JunitCase) -> str | None:
    base_outcome = None if base is None else base.outcome
    if tip.outcome in _FAILING and base_outcome not in _FAILING:
        return "new_fail"
    if tip.outcome == "passed" and base_outcome in {*_FAILING, "skipped"}:
        return "new_pass"
    if base is None:
        return "changed"
    if base.outcome != tip.outcome:
        return "changed"
    if tip.outcome in _FAILING and base.message != tip.message:
        return "changed"
    return None


def compare_junit(base: Path, tip: Path) -> JunitCompare:
    """New fails, new passes, and other outcome changes from base to tip."""

    base_cases = load_junit(base)
    tip_cases = load_junit(tip)
    changes: list[OutcomeChange] = []
    for case_id in sorted(set(base_cases) | set(tip_cases)):
        before = base_cases.get(case_id)
        after = tip_cases.get(case_id)
        if after is None:
            assert before is not None
            changes.append(
                OutcomeChange(
                    kind="changed",
                    case_id=case_id,
                    base_outcome=before.outcome,
                    tip_outcome="",
                    base_message=before.message,
                    tip_message="",
                )
            )
            continue
        kind = _kind(before, after)
        if kind is None:
            continue
        changes.append(
            OutcomeChange(
                kind=kind,
                case_id=case_id,
                base_outcome="" if before is None else before.outcome,
                tip_outcome=after.outcome,
                base_message="" if before is None else before.message,
                tip_message=after.message,
            )
        )
    return JunitCompare(
        base_cases=len(base_cases),
        tip_cases=len(tip_cases),
        base_failing=sum(case.outcome in _FAILING for case in base_cases.values()),
        tip_failing=sum(case.outcome in _FAILING for case in tip_cases.values()),
        base_skipped=sum(case.outcome == "skipped" for case in base_cases.values()),
        tip_skipped=sum(case.outcome == "skipped" for case in tip_cases.values()),
        changes=tuple(changes),
    )


def _print_report(report: JunitCompare, out: TextIO) -> None:
    print(
        f"base_cases\t{report.base_cases}\tbase_failing\t{report.base_failing}\t"
        f"base_skipped\t{report.base_skipped}",
        file=out,
    )
    print(
        f"tip_cases\t{report.tip_cases}\ttip_failing\t{report.tip_failing}\t"
        f"tip_skipped\t{report.tip_skipped}",
        file=out,
    )
    for kind in ("new_fail", "new_pass", "changed"):
        count = sum(item.kind == kind for item in report.changes)
        print(f"{kind}\t{count}", file=out)
    for item in report.changes:
        print(
            f"{item.kind}\t{item.case_id}\t{item.base_outcome}\t{item.tip_outcome}",
            file=out,
        )
        if item.base_message:
            print(f"base_message\t{item.base_message.replace(chr(10), ' / ')}", file=out)
        if item.tip_message:
            print(f"tip_message\t{item.tip_message.replace(chr(10), ' / ')}", file=out)


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", type=Path, help="junit XML for the baseline")
    parser.add_argument("tip", type=Path, help="junit XML for the comparison")
    args = parser.parse_args(list(argv) if argv is not None else None)
    sink = sys.stdout if out is None else out
    try:
        report = compare_junit(args.base, args.tip)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print_report(report, sink)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
