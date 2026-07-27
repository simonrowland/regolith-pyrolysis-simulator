#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def loadgroup_suffix(name: str) -> str | None:
    at = name.rfind("@")
    return name[at + 1 :] if at > name.rfind("]") else None


def suite_group_durations(path: Path) -> dict[str, float]:
    durations: defaultdict[str, float] = defaultdict(float)
    for testcase in ET.parse(path).getroot().iter("testcase"):
        group = loadgroup_suffix(testcase.get("name", ""))
        if group is not None:
            durations[group] += float(testcase.get("time", "0"))
    return dict(durations)


def expand_inputs(inputs: list[Path]) -> list[Path]:
    paths: set[Path] = set()
    for path in inputs:
        if path.is_dir():
            paths.update(path.glob("*.xml"))
        else:
            paths.add(path)
    return sorted(paths)


def build_duration_hints(paths: list[Path]) -> dict[str, float]:
    observations: defaultdict[str, list[float]] = defaultdict(list)
    for path in paths:
        for group, seconds in suite_group_durations(path).items():
            observations[group].append(seconds)
    return {
        group: round(statistics.median(seconds), 3)
        for group, seconds in sorted(observations.items())
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate stable pytest-xdist loadgroup duration hints from JUnit XML."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="JUnit XML files or directories containing XML files",
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    paths = expand_inputs(args.inputs)
    if not paths:
        parser.error("no JUnit XML files found")
    payload = {
        "schema": 1,
        "aggregation": "median of each loadgroup's per-suite total seconds",
        "source_files": [path.name for path in paths],
        "durations_seconds": build_duration_hints(paths),
    }
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
